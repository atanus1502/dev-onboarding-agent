"""Settings loaded from the environment plus a YAML file describing the knowledge sources."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

Effort = Literal["low", "medium", "high", "xhigh", "max"]
_EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class RepoSource:
    full_name: str  # "owner/name"
    default_branch: str = "main"
    # Architecture docs / ADR folders, surfaced to the model as places worth searching.
    docs_paths: tuple[str, ...] = ()
    # True when every employee can read the repo. Only affects whether an answer citing it may
    # be posted publicly in a channel; access itself is always checked live per user.
    org_wide: bool = False


@dataclass(frozen=True)
class WikiSpace:
    key: str
    org_wide: bool = False


@dataclass(frozen=True)
class Settings:
    anthropic_model: str
    effort: Effort
    # Either a static token, or a GitHub App (id + private key + installation) that mints
    # short-lived installation tokens.
    github_token: str | None
    github_api_url: str
    github_web_url: str
    # Confluence is optional; without it the agent answers from GitHub only.
    confluence_base_url: str | None
    confluence_email: str | None
    confluence_api_token: str | None
    repos: tuple[RepoSource, ...]
    spaces: tuple[WikiSpace, ...]
    identities_path: Path
    db_path: Path
    github_app_id: str | None = None
    github_app_private_key_path: Path | None = None
    github_app_installation_id: str | None = None
    permission_cache_ttl: float = 300.0
    extra: dict[str, Any] = field(default_factory=dict)

    def repo(self, full_name: str) -> RepoSource | None:
        return next((r for r in self.repos if r.full_name.lower() == full_name.lower()), None)

    def space(self, key: str) -> WikiSpace | None:
        return next((s for s in self.spaces if s.key.lower() == key.lower()), None)

    @classmethod
    def from_env(cls, sources_path: Path | None = None) -> Settings:
        path = sources_path or Path(os.environ.get("ONBOARDING_SOURCES", "config/sources.yaml"))
        raw = yaml.safe_load(path.read_text()) or {}
        repos = tuple(
            RepoSource(
                full_name=r["name"],
                default_branch=r.get("default_branch", "main"),
                docs_paths=tuple(r.get("docs_paths", ())),
                org_wide=bool(r.get("org_wide", False)),
            )
            for r in raw.get("repos", [])
        )
        spaces = tuple(
            WikiSpace(key=s["key"], org_wide=bool(s.get("org_wide", False)))
            for s in raw.get("confluence_spaces", [])
        )
        effort = os.environ.get("ONBOARDING_EFFORT", "medium")
        if effort not in _EFFORTS:
            raise ValueError(f"ONBOARDING_EFFORT must be one of {_EFFORTS}, got {effort!r}")
        return cls(
            anthropic_model=os.environ.get("ONBOARDING_MODEL", "claude-opus-5-5"),
            effort=cast(Effort, effort),
            github_token=os.environ.get("GITHUB_TOKEN") or None,
            github_app_id=os.environ.get("GITHUB_APP_ID") or None,
            github_app_private_key_path=(
                Path(p) if (p := os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")) else None
            ),
            github_app_installation_id=os.environ.get("GITHUB_APP_INSTALLATION_ID") or None,
            github_api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            github_web_url=os.environ.get("GITHUB_WEB_URL", "https://github.com"),
            confluence_base_url=os.environ.get("CONFLUENCE_BASE_URL", "").rstrip("/") or None,
            confluence_email=os.environ.get("CONFLUENCE_EMAIL") or None,
            confluence_api_token=os.environ.get("CONFLUENCE_API_TOKEN") or None,
            repos=repos,
            spaces=spaces,
            identities_path=Path(os.environ.get("ONBOARDING_IDENTITIES", "config/identities.yaml")),
            db_path=Path(os.environ.get("ONBOARDING_DB", "onboarding.db")),
        )
