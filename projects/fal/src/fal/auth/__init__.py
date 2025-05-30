from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from fal.auth import auth0, local
from fal.config import Config
from fal.exceptions import FalServerlessException
from fal.exceptions.auth import UnauthenticatedException


class GoogleColabState:
    def __init__(self):
        self.is_checked = False
        self.lock = Lock()
        self.secret: Optional[str] = None


_colab_state = GoogleColabState()


def is_google_colab() -> bool:
    try:
        from IPython import get_ipython

        return "google.colab" in str(get_ipython())
    except ModuleNotFoundError:
        return False
    except NameError:
        return False


def get_colab_token() -> Optional[str]:
    if not is_google_colab():
        return None
    with _colab_state.lock:
        if _colab_state.is_checked:  # request access only once
            return _colab_state.secret

        try:
            from google.colab import userdata  # noqa: I001
        except ImportError:
            return None

        try:
            token = userdata.get("FAL_KEY")
            _colab_state.secret = token.strip()
        except Exception:
            _colab_state.secret = None

        _colab_state.is_checked = True
        return _colab_state.secret


def key_credentials() -> tuple[str, str] | None:
    # Ignore key credentials when the user forces auth by user.
    if os.environ.get("FAL_FORCE_AUTH_BY_USER") == "1":
        return None

    config = Config()

    key = os.environ.get("FAL_KEY") or config.get("key") or get_colab_token()
    if key:
        key_id, key_secret = key.split(":", 1)
        return (key_id, key_secret)
    elif "FAL_KEY_ID" in os.environ and "FAL_KEY_SECRET" in os.environ:
        return (os.environ["FAL_KEY_ID"], os.environ["FAL_KEY_SECRET"])
    else:
        return None


def _fetch_access_token() -> str:
    """
    Load the refresh token, request a new access_token (refreshing the refresh token)
    and return the access_token.
    """
    # We need to lock both read and write access because we could be reading a soon
    # invalid refresh_token
    with local.lock_token():
        refresh_token, access_token = local.load_token()

        if refresh_token is None:
            raise UnauthenticatedException()

        if access_token is not None:
            try:
                auth0.verify_access_token_expiration(access_token)
                return access_token
            except Exception:
                # access_token expired, will refresh
                pass

        try:
            token_data = auth0.refresh(refresh_token)

            # NOTE: Auth0 Refresh Token Rotation enabled
            # So the old refresh_token is no longer valid
            local.save_token(token_data["refresh_token"], token_data["access_token"])
        except:
            local.delete_token()
            raise

        return token_data["access_token"]


def _fetch_teams(bearer_token: str) -> list[dict]:
    import json
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from fal.exceptions import FalServerlessException
    from fal.flags import REST_URL

    request = Request(
        method="GET",
        url=f"{REST_URL}/users/teams",
        headers={"Authorization": bearer_token},
    )
    try:
        with urlopen(request) as response:
            return json.load(response)
    except HTTPError as exc:
        raise FalServerlessException("Failed to fetch teams") from exc


def login(console):
    token_data = auth0.login(console)
    with local.lock_token():
        local.save_token(token_data["refresh_token"])


def logout(console):
    refresh_token, _ = local.load_token()
    if refresh_token is None:
        raise FalServerlessException("You're not logged in")
    auth0.revoke(refresh_token, console)
    with local.lock_token():
        local.delete_token()


@dataclass
class UserAccess:
    _access_token: str | None = field(repr=False, default=None)
    _user_info: dict | None = field(repr=False, default=None)
    _exc: Exception | None = field(repr=False, default=None)
    _accounts: list[dict] | None = field(repr=False, default=None)

    @property
    def info(self) -> dict:
        if self._user_info is None:
            self._user_info = auth0.get_user_info(self.bearer_token)

        return self._user_info

    @property
    def access_token(self) -> str:
        if self._exc is not None:
            # We access this several times, so we want to raise the
            # original exception instead of the newer exceptions we
            # would get from the effects of the original exception.
            raise self._exc

        if self._access_token is None:
            try:
                self._access_token = _fetch_access_token()
            except Exception as e:
                self._exc = e
                raise

        return self._access_token

    @property
    def bearer_token(self) -> str:
        return "Bearer " + self.access_token

    @property
    def accounts(self) -> list[dict]:
        if self._accounts is None:
            self._accounts = _fetch_teams(self.bearer_token)
            self._accounts = sorted(
                self._accounts, key=lambda x: (not x["is_personal"], x["nickname"])
            )

        return self._accounts

    def get_account(self, team: str) -> dict:
        for t in self.accounts:
            if t["nickname"].lower() == team.lower():
                return t
        raise ValueError(f"Team {team} not found")
