from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from garmin_runner.config import GarminSettings, get_credentials


class GarminRunnerLoginError(RuntimeError):
    """Raised when Garmin Connect authentication fails."""


MfaCallback = Callable[[], str]


def create_garmin_client(
    settings: GarminSettings,
    mfa_callback: MfaCallback | None = None,
) -> Any:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )

    token_store = Path(settings.token_store)
    token_store.mkdir(parents=True, exist_ok=True)

    api = Garmin()
    try:
        api.login(str(token_store))
        return api
    except GarminConnectTooManyRequestsError as exc:
        raise GarminRunnerLoginError(
            "Garmin 登录失败：登录请求过于频繁，请等待几分钟后重试。"
        ) from exc
    except (GarminConnectAuthenticationError, GarminConnectConnectionError):
        email, password = get_credentials(settings)
        if not email or not password:
            raise GarminRunnerLoginError(
                "Garmin 登录失败：未能使用本地 token 登录，且没有找到账号环境变量。"
                f"请在 .env 中设置 {settings.email_env} 和 {settings.password_env}。"
            ) from None

    try:
        api = Garmin(email, password, is_cn=False, prompt_mfa=mfa_callback)
        api.login(str(token_store))
        return api
    except GarminConnectAuthenticationError as exc:
        raise GarminRunnerLoginError(
            "Garmin 登录失败：请检查账号、密码、MFA 或 Garmin Connect 状态。"
        ) from exc
    except GarminConnectConnectionError as exc:
        raise GarminRunnerLoginError(
            "Garmin Connect 连接失败：请检查网络或稍后重试。"
        ) from exc
    except GarminConnectTooManyRequestsError as exc:
        raise GarminRunnerLoginError(
            "Garmin 登录失败：登录请求过于频繁，请等待几分钟后重试。"
        ) from exc


def list_running_activities(api: Any, since: str, until: str) -> list[dict[str, Any]]:
    return api.get_activities_by_date(
        since,
        until,
        activitytype="running",
        sortorder="asc",
    )


def download_original_activity(api: Any, activity_id: str) -> bytes:
    from garminconnect import Garmin

    return api.download_activity(
        activity_id,
        dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
    )
