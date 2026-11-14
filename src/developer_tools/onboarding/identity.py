"""Maps a Slack user to the accounts whose permissions the agent borrows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml


@dataclass(frozen=True)
class Identity:
    slack_user_id: str
    github_login: str | None = None
    atlassian_account_id: str | None = None

    @property
    def linked(self) -> bool:
        return self.github_login is not None or self.atlassian_account_id is not None


class IdentityResolver(Protocol):
    def resolve(self, slack_user_id: str) -> Identity: ...


class StaticIdentityResolver:
    """Reads a YAML mapping of Slack user id -> {github, atlassian}.

    Unknown users resolve to an unlinked Identity, which every permission check denies.
    """

    def __init__(self, mapping: dict[str, dict[str, str]]) -> None:
        self._mapping = mapping

    @classmethod
    def from_file(cls, path: Path) -> StaticIdentityResolver:
        if not path.exists():
            return cls({})
        return cls(yaml.safe_load(path.read_text()) or {})

    def resolve(self, slack_user_id: str) -> Identity:
        entry = self._mapping.get(slack_user_id, {})
        return Identity(
            slack_user_id=slack_user_id,
            github_login=entry.get("github"),
            atlassian_account_id=entry.get("atlassian"),
        )
