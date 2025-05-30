from __future__ import annotations

from structlog.typing import EventDict, WrappedLogger


class AddUserIdProcessor:
    def __init__(self):
        from fal.auth import UserAccess

        self.user_access = UserAccess()

    def __call__(
        self, logger: WrappedLogger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        """The structlog processor that sends the logged user id on every log"""
        user_id: str | None = None
        try:
            user_id = self.user_access.info.get("sub")
        except Exception:
            # logs are fail-safe, so any exception is safe to ignore
            # this is expected to happen only when user is logged out
            # or there's no internet connection
            pass
        event_dict["usr.id"] = user_id
        return event_dict
