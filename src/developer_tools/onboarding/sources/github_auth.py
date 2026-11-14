"""GitHub credentials: a static token, or a GitHub App installation token refreshed on demand.

Installation tokens last an hour, so a long-running Slack server must mint new ones. The
provider is called before every request and only hits GitHub when the cached token is within
five minutes of expiry.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime

import httpx
import jwt

TokenProvider = Callable[[], str]

_REFRESH_MARGIN = 300.0


def static_token(token: str) -> TokenProvider:
    return lambda: token


class AppInstallationToken:
    def __init__(
        self,
        app_id: str,
        private_key_pem: str,
        installation_id: str,
        api_url: str = "https://api.github.com",
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._app_id = app_id
        self._key = private_key_pem
        self._installation_id = installation_id
        self._api = api_url.rstrip("/")
        self._http = http or httpx.Client(timeout=20.0)
        self._clock = clock
        self._lock = threading.Lock()  # Bolt runs listeners on a thread pool
        self._token: str | None = None
        self._expires_at = 0.0

    def __call__(self) -> str:
        with self._lock:
            if self._token is None or self._clock() > self._expires_at - _REFRESH_MARGIN:
                self._refresh()
            assert self._token is not None
            return self._token

    def app_jwt(self) -> str:
        now = int(self._clock())
        # iat is backdated to tolerate clock drift; GitHub caps exp at 10 minutes.
        payload = {"iat": now - 60, "exp": now + 540, "iss": self._app_id}
        return jwt.encode(payload, self._key, algorithm="RS256")

    def _refresh(self) -> None:
        resp = self._http.post(
            f"{self._api}/app/installations/{self._installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self.app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = str(data["token"])
        self._expires_at = datetime.fromisoformat(
            data["expires_at"].replace("Z", "+00:00")
        ).timestamp()
