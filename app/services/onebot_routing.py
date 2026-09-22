from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class OneBotRoute:
    self_id: str
    api_base: str
    access_token: str
    webhook_token: str


_current_self_id: ContextVar[str] = ContextVar("onebot_self_id", default="")


def set_current_onebot_self_id(self_id: str) -> None:
    _current_self_id.set(str(self_id or "").strip())


def current_onebot_self_id() -> str:
    return _current_self_id.get().strip()


def onebot_route(settings, self_id: str | None = None) -> OneBotRoute:
    selected = str(self_id or current_onebot_self_id() or settings.onebot_self_id or "").strip()
    secondary_id = str(getattr(settings, "onebot_self_id_2", "") or "").strip()

    if secondary_id and selected == secondary_id:
        return OneBotRoute(
            self_id=secondary_id,
            api_base=str(getattr(settings, "onebot_api_base_2", "") or settings.onebot_api_base or "").strip(),
            access_token=str(getattr(settings, "onebot_access_token_2", "") or "").strip(),
            webhook_token=str(getattr(settings, "onebot_webhook_token_2", "") or settings.onebot_webhook_token or "").strip(),
        )

    return OneBotRoute(
        self_id=str(settings.onebot_self_id or selected or "").strip(),
        api_base=str(settings.onebot_api_base or "").strip(),
        access_token=str(settings.onebot_access_token or "").strip(),
        webhook_token=str(settings.onebot_webhook_token or "").strip(),
    )
