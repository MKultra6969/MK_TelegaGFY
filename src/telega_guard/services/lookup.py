from __future__ import annotations

import asyncio
import logging
from time import time

import aiohttp

from telega_guard.repositories.lookup_cache import LookupCacheRepository

LOGGER = logging.getLogger(__name__)

CALLS_BASE_URL = "https://calls.okcdn.ru"
CALLS_API_KEY = "CHKIPMKGDIHBABABA"
SESSION_DATA = (
    '{"device_id":"telega_alert","version":2,"client_version":"android_8","client_type":"SDK_ANDROID"}'
)


class CallsLookupService:
    def __init__(
        self,
        repository: LookupCacheRepository,
        *,
        cache_ttl_seconds: int,
        failure_cooldown_seconds: int,
    ) -> None:
        self.repository = repository
        self.cache_ttl_seconds = cache_ttl_seconds
        self.failure_cooldown_seconds = failure_cooldown_seconds
        self._session: aiohttp.ClientSession | None = None
        self._inflight: dict[int, asyncio.Task[bool | None]] = {}
        self._inflight_lock = asyncio.Lock()

    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(total=10, sock_connect=3.1, sock_read=6)
        self._session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def lookup_used_before(self, user_id: int) -> bool | None:
        if user_id <= 0:
            return None

        entry = await self.repository.get_entry(user_id)
        now = int(time())
        if entry is not None:
            if entry.used_before is not None and entry.checked_at > 0:
                if (now - entry.checked_at) <= self.cache_ttl_seconds:
                    return entry.used_before
            if entry.failure_retry_after > now:
                LOGGER.debug("Lookup cooldown is still active for user %s", user_id)
                return None

        async with self._inflight_lock:
            existing = self._inflight.get(user_id)
            if existing is not None:
                return await existing
            task = asyncio.create_task(self._lookup_and_persist(user_id))
            self._inflight[user_id] = task
        try:
            return await task
        finally:
            async with self._inflight_lock:
                self._inflight.pop(user_id, None)

    async def _lookup_and_persist(self, user_id: int) -> bool | None:
        try:
            used_before = await self._perform_lookup(user_id)
        except Exception:
            LOGGER.exception("Historical Telega lookup failed for user %s", user_id)
            await self.repository.set_failure(
                user_id,
                int(time()) + self.failure_cooldown_seconds,
            )
            return None

        await self.repository.set_result(user_id, used_before)
        return used_before

    async def _perform_lookup(self, user_id: int) -> bool:
        auth_payload = await self._post_form(
            f"{CALLS_BASE_URL}/api/auth/anonymLogin",
            {
                "application_key": CALLS_API_KEY,
                "session_data": SESSION_DATA,
            },
        )
        session_key = str((auth_payload or {}).get("session_key") or "").strip()
        if not session_key:
            raise RuntimeError("OK Calls auth did not return session_key")

        result_payload = await self._post_form(
            f"{CALLS_BASE_URL}/api/vchat/getOkIdsByExternalIds",
            {
                "application_key": CALLS_API_KEY,
                "session_key": session_key,
                "externalIds": '[{"id":"%s","ok_anonym":false}]' % int(user_id),
            },
        )
        ids = (result_payload or {}).get("ids") or []
        target = str(int(user_id))
        for item in ids:
            external = (item or {}).get("external_user_id") or {}
            if str(external.get("id") or "") == target:
                return True
        return False

    async def _post_form(self, url: str, data: dict[str, str]) -> dict:
        if self._session is None:
            raise RuntimeError("CallsLookupService was not started")
        async with self._session.post(
            url,
            data=data,
            headers={"Accept": "application/json"},
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)
