from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram import Bot, html
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import ChatMemberOwner
from aiogram.utils.chat_member import ADMINS

from telega_guard.models import ChatSettings, JoinCandidate, ModerationDecision, TelegaCheckResult
from telega_guard.repositories.chat_settings import ChatSettingsRepository
from telega_guard.repositories.moderation_events import ModerationEventRepository
from telega_guard.services.detector import TelethonDetectorService

LOGGER = logging.getLogger(__name__)

STATUS_PUBLIC_REASONS = {
    "active_telega_session": "обнаружена активная сессия Telega",
    "used_telega_before": "обнаружено прошлое использование Telega",
}

STATUS_ADMIN_LABELS = {
    "active_telega_session": "Имеет активную сессию",
    "used_telega_before": "Использовал Telega раньше",
}

STATUS_CHAT_LABELS = {
    "active_telega_session": "использует Telega с активной сессией",
    "used_telega_before": "использовал Telega раньше",
}


class ModerationCoordinator:
    def __init__(
        self,
        *,
        bot: Bot,
        settings_repository: ChatSettingsRepository,
        event_repository: ModerationEventRepository,
        detector: TelethonDetectorService,
        duplicate_ttl_seconds: int,
    ) -> None:
        self.bot = bot
        self.settings_repository = settings_repository
        self.event_repository = event_repository
        self.detector = detector
        self.duplicate_ttl_seconds = duplicate_ttl_seconds
        self._queue: asyncio.Queue[JoinCandidate] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._seen_pairs: dict[tuple[int, int], float] = {}

    async def start(self) -> None:
        self._worker = asyncio.create_task(self._run(), name="moderation-coordinator")

    async def close(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    async def enqueue(self, candidate: JoinCandidate) -> None:
        if candidate.user_id <= 0 or candidate.user_is_bot:
            return
        LOGGER.info(
            "moderation_enqueue chat_id=%s chat_type=%s user_id=%s source=%s",
            candidate.chat_id,
            candidate.chat_type,
            candidate.user_id,
            candidate.source,
        )
        await self._queue.put(candidate)

    async def _run(self) -> None:
        while True:
            candidate = await self._queue.get()
            try:
                await self._handle_candidate(candidate)
            except Exception:
                LOGGER.exception(
                    "Failed to process join candidate chat=%s user=%s",
                    candidate.chat_id,
                    candidate.user_id,
                )
            finally:
                self._queue.task_done()

    async def _handle_candidate(self, candidate: JoinCandidate) -> None:
        if self._is_duplicate(candidate.chat_id, candidate.user_id):
            return

        settings = await self.settings_repository.get(candidate.chat_id)
        if settings is None or not settings.has_active_rules:
            return

        is_admin = await self._is_admin(candidate.chat_id, candidate.user_id)
        if is_admin is None:
            LOGGER.warning(
                "Skipping moderation because admin status could not be verified chat=%s user=%s",
                candidate.chat_id,
                candidate.user_id,
            )
            return
        if is_admin:
            LOGGER.info(
                "Skipping Telega moderation for admin user=%s in chat=%s",
                candidate.user_id,
                candidate.chat_id,
            )
            return

        result = await self.detector.check_user(
            chat_id=candidate.chat_id,
            user_id=candidate.user_id,
            user_entity=candidate.user_entity,
        )
        decision = self._make_decision(settings, result)
        if decision.should_ban:
            await self._ban(candidate, result, decision)
            return

        if decision.should_notify_admin:
            await self._notify_admin(candidate, result, decision)
            if self._should_publish_chat_alert(settings, candidate):
                await self._announce_detection_to_chat(candidate, result, decision)
            return

        if not decision.should_ban:
            LOGGER.info(
                "User %s passed moderation in chat %s: active=%s used_before=%s",
                candidate.user_id,
                candidate.chat_id,
                result.active_now,
                result.used_before,
            )
            return

    def _make_decision(self, settings: ChatSettings, result: TelegaCheckResult) -> ModerationDecision:
        matched_statuses = self._matched_statuses(result)
        ban_statuses = tuple(
            status
            for status in matched_statuses
            if (
                status == "active_telega_session"
                and settings.ban_if_active_now
            )
            or (
                status == "used_telega_before"
                and settings.ban_if_used_before
            )
        )
        if ban_statuses:
            return ModerationDecision(
                should_ban=True,
                reason=(
                    ban_statuses[0]
                    if len(ban_statuses) == 1
                    else "multiple_telega_signals"
                ),
                public_reason=self._public_reason_text(ban_statuses),
                matched_statuses=ban_statuses,
            )
        if settings.notify_admin_on_detection and matched_statuses:
            return ModerationDecision(
                should_ban=False,
                should_notify_admin=True,
                reason=matched_statuses[0] if len(matched_statuses) == 1 else "telega_detected",
                matched_statuses=matched_statuses,
            )
        return ModerationDecision(should_ban=False)

    async def _ban(
        self,
        candidate: JoinCandidate,
        result: TelegaCheckResult,
        decision: ModerationDecision,
    ) -> None:
        details = self._serialize_result(result)
        try:
            await self.bot.ban_chat_member(candidate.chat_id, candidate.user_id)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            LOGGER.warning(
                "Ban failed for user=%s in chat=%s: %s",
                candidate.user_id,
                candidate.chat_id,
                exc,
            )
            await self.event_repository.add_event(
                chat_id=candidate.chat_id,
                user_id=candidate.user_id,
                action="ban",
                reason=decision.reason or "unknown",
                result="failed",
                details={**details, "error": str(exc)},
            )
            return

        LOGGER.info(
            "Banned user=%s in chat=%s because of %s",
            candidate.user_id,
            candidate.chat_id,
            decision.reason,
        )
        await self.event_repository.add_event(
            chat_id=candidate.chat_id,
            user_id=candidate.user_id,
            action="ban",
            reason=decision.reason or "unknown",
            result="success",
            details=details,
        )

        if candidate.chat_type != "channel":
            try:
                await self.bot.send_message(
                    candidate.chat_id,
                    (
                        "<b>Пользователь заблокирован</b>\n"
                        f"<b>Пользователь:</b> <a href=\"tg://user?id={candidate.user_id}\">{candidate.user_id}</a>\n"
                        f"{self._render_chat_reason_block(decision)}"
                    ),
                    disable_web_page_preview=True,
                )
            except Exception:
                LOGGER.debug("Could not send moderation notification", exc_info=True)

    async def _notify_admin(
        self,
        candidate: JoinCandidate,
        result: TelegaCheckResult,
        decision: ModerationDecision,
    ) -> None:
        details = self._serialize_result(result)
        owner_user_id = await self._get_chat_owner_id(candidate.chat_id)
        if owner_user_id is None:
            LOGGER.warning(
                "Notification skipped because chat owner was not found for chat=%s",
                candidate.chat_id,
            )
            await self.event_repository.add_event(
                chat_id=candidate.chat_id,
                user_id=candidate.user_id,
                action="notify",
                reason=decision.reason or "unknown",
                result="failed",
                details={**details, "error": "chat owner not found"},
            )
            return

        try:
            await self.bot.send_message(
                owner_user_id,
                self._render_admin_notification(candidate, decision),
                disable_web_page_preview=True,
            )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            LOGGER.warning(
                "Notify failed for user=%s in chat=%s: %s",
                candidate.user_id,
                candidate.chat_id,
                exc,
            )
            await self.event_repository.add_event(
                chat_id=candidate.chat_id,
                user_id=candidate.user_id,
                action="notify",
                reason=decision.reason or "unknown",
                result="failed",
                details={**details, "target_user_id": owner_user_id, "error": str(exc)},
            )
            return

        LOGGER.info(
            "Sent admin notification for user=%s in chat=%s statuses=%s",
            candidate.user_id,
            candidate.chat_id,
            ",".join(decision.matched_statuses),
        )
        await self.event_repository.add_event(
            chat_id=candidate.chat_id,
            user_id=candidate.user_id,
            action="notify",
            reason=decision.reason or "unknown",
            result="success",
            details={**details, "target_user_id": owner_user_id},
        )

    async def _announce_detection_to_chat(
        self,
        candidate: JoinCandidate,
        result: TelegaCheckResult,
        decision: ModerationDecision,
    ) -> None:
        details = self._serialize_result(result)
        try:
            await self.bot.send_message(
                candidate.chat_id,
                self._render_chat_alert(candidate, decision),
                disable_web_page_preview=True,
            )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            LOGGER.warning(
                "Chat alert failed for user=%s in chat=%s: %s",
                candidate.user_id,
                candidate.chat_id,
                exc,
            )
            await self.event_repository.add_event(
                chat_id=candidate.chat_id,
                user_id=candidate.user_id,
                action="chat_notice",
                reason=decision.reason or "unknown",
                result="failed",
                details={**details, "error": str(exc)},
            )
            return

        await self.event_repository.add_event(
            chat_id=candidate.chat_id,
            user_id=candidate.user_id,
            action="chat_notice",
            reason=decision.reason or "unknown",
            result="success",
            details=details,
        )

    async def _is_admin(self, chat_id: int, user_id: int) -> bool | None:
        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
        except TelegramRetryAfter as exc:
            LOGGER.warning(
                "Telegram requested backoff for admin check: %ss (chat=%s user=%s)",
                exc.retry_after,
                chat_id,
                user_id,
            )
            return None
        except Exception:
            return None
        return isinstance(member, ADMINS)

    def _is_duplicate(self, chat_id: int, user_id: int) -> bool:
        now = time.monotonic()
        stale = [
            key
            for key, timestamp in self._seen_pairs.items()
            if (now - timestamp) > self.duplicate_ttl_seconds
        ]
        for key in stale:
            self._seen_pairs.pop(key, None)

        key = (chat_id, user_id)
        if key in self._seen_pairs:
            return True
        self._seen_pairs[key] = now
        return False

    async def _get_chat_owner_id(self, chat_id: int) -> int | None:
        try:
            administrators = await self.bot.get_chat_administrators(chat_id)
        except Exception:
            LOGGER.debug("Could not fetch chat owner for chat=%s", chat_id, exc_info=True)
            return None

        for member in administrators:
            if isinstance(member, ChatMemberOwner):
                return member.user.id
        return None

    @staticmethod
    def _matched_statuses(result: TelegaCheckResult) -> tuple[str, ...]:
        matched: list[str] = []
        if result.active_now is True:
            matched.append("active_telega_session")
        if result.used_before is True:
            matched.append("used_telega_before")
        return tuple(matched)

    @staticmethod
    def _render_admin_notification(
        candidate: JoinCandidate,
        decision: ModerationDecision,
    ) -> str:
        statuses = "\n".join(
            f"• {html.quote(STATUS_ADMIN_LABELS[status])}"
            for status in decision.matched_statuses
        )
        return (
            "<b>Найден пользователь с Telega-статусом</b>\n"
            f"<b>Чат:</b> <code>{html.quote(candidate.title or str(candidate.chat_id))}</code>\n"
            f"<b>ID чата:</b> <code>{candidate.chat_id}</code>\n"
            f"<b>Пользователь:</b> <a href=\"tg://user?id={candidate.user_id}\">{candidate.user_id}</a>\n\n"
            "<b>Статусы</b>\n"
            f"{statuses}\n\n"
            "<i>Автобан не применялся: включено только уведомление.</i>"
        )

    @staticmethod
    def _should_publish_chat_alert(settings: ChatSettings, candidate: JoinCandidate) -> bool:
        return (
            candidate.chat_type != "channel"
            and settings.notify_admin_on_detection
            and not settings.ban_if_active_now
            and not settings.ban_if_used_before
        )

    @staticmethod
    def _public_reason_text(statuses: tuple[str, ...]) -> str:
        if not statuses:
            return "обнаружен Telega-статус"
        if len(statuses) == 1:
            return STATUS_PUBLIC_REASONS[statuses[0]]
        joined = "; ".join(STATUS_PUBLIC_REASONS[status] for status in statuses)
        return f"обнаружены несколько причин: {joined}"

    @staticmethod
    def _render_chat_reason_block(decision: ModerationDecision) -> str:
        if len(decision.matched_statuses) <= 1:
            return f"<b>Причина:</b> {html.quote(decision.public_reason or 'не указана')}"
        reasons = "\n".join(
            f"• {html.quote(STATUS_PUBLIC_REASONS[status])}"
            for status in decision.matched_statuses
        )
        return f"<b>Причины:</b>\n{reasons}"

    @staticmethod
    def _render_chat_alert(candidate: JoinCandidate, decision: ModerationDecision) -> str:
        statuses = "\n".join(
            f"• {html.quote(STATUS_CHAT_LABELS[status])}"
            for status in decision.matched_statuses
        )
        return (
            "<b>Обнаружен пользователь с Telega-статусом</b>\n"
            f"<b>Пользователь:</b> <a href=\"tg://user?id={candidate.user_id}\">{candidate.user_id}</a>\n"
            "<b>Статусы:</b>\n"
            f"{statuses}\n\n"
            "<i>Автобан отключён, поэтому отправлено только уведомление.</i>"
        )

    @staticmethod
    def _serialize_result(result: TelegaCheckResult) -> dict[str, Any]:
        return {
            "active_now": result.active_now,
            "used_before": result.used_before,
            "active_supported": result.active_supported,
            "active_source": result.active_source,
            "used_before_source": result.used_before_source,
            "notes": list(result.notes),
            "checked_at": result.checked_at.isoformat(),
        }
