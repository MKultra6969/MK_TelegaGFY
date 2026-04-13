from aiogram.filters.callback_data import CallbackData


class SettingsCallback(CallbackData, prefix="cfg"):
    action: str
    chat_id: int = 0
    flag: str = "-"


class LogsCallback(CallbackData, prefix="logs"):
    action: str
    chat_id: int = 0


class BroadcastCallback(CallbackData, prefix="broadcast"):
    action: str
    token: str
