import json

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from developer_tools.onboarding.sources.github_auth import AppInstallationToken


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_installation_token_is_cached_then_refreshed_near_expiry() -> None:
    now = [1_800_000_000.0]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = {"token": f"tok{len(calls)}", "expires_at": "2027-01-15T08:53:20Z"}  # now + 1h
        return httpx.Response(201, content=json.dumps(body))

    provider = AppInstallationToken(
        "123",
        _pem(),
        "456",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: now[0],
    )
    assert provider() == "tok1"
    assert provider() == "tok1"
    assert calls[0].url.path == "/app/installations/456/access_tokens"
    claims = jwt.decode(
        calls[0].headers["Authorization"].split()[1], options={"verify_signature": False}
    )
    assert claims["iss"] == "123" and claims["exp"] - claims["iat"] <= 600

    now[0] += 3600 - 200  # inside the 5-minute refresh margin
    assert provider() == "tok2"
