"""Per-user access checks that mirror GitHub and Confluence permissions.

The bot's own credentials can read everything it's configured for, so every tool call must go
through these checks with the asking user's identity. Checks fail closed: an unlinked account,
an API error or a timeout all mean "no access".
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from developer_tools.onboarding.identity import Identity
from developer_tools.onboarding.sources.confluence import ConfluenceClient
from developer_tools.onboarding.sources.github import GitHubClient

log = logging.getLogger(__name__)

_READABLE = {"admin", "maintain", "write", "triage", "read"}


class PermissionChecker(Protocol):
    def can_read_repo(self, identity: Identity, repo: str) -> bool: ...
    def can_read_page(self, identity: Identity, page_id: str) -> bool: ...


class LivePermissionChecker:
    def __init__(
        self,
        github: GitHubClient,
        confluence: ConfluenceClient | None,
        ttl: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._github = github
        self._confluence = confluence
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[tuple[str, str, str], tuple[float, bool]] = {}

    def can_read_repo(self, identity: Identity, repo: str) -> bool:
        login = identity.github_login
        if not login:
            return False
        # Any GitHub user can read a public repo, so no per-user lookup is needed (and the
        # collaborator endpoint needs repo-scoped token access that public-only tokens lack).
        if self._cached(
            ("public", "", repo.lower()), lambda: self._github.visibility(repo) == "public"
        ):
            return True
        return self._cached(
            ("repo", login, repo.lower()),
            lambda: self._github.collaborator_permission(repo, login) in _READABLE,
        )

    def can_read_page(self, identity: Identity, page_id: str) -> bool:
        account = identity.atlassian_account_id
        confluence = self._confluence
        if not account or confluence is None:
            return False
        return self._cached(
            ("page", account, page_id), lambda: confluence.can_read(page_id, account)
        )

    def _cached(self, key: tuple[str, str, str], check: Callable[[], bool]) -> bool:
        now = self._clock()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        try:
            allowed = check()
        except Exception as e:
            log.warning("permission check failed for %s; denying: %s", key, e)
            return False  # don't cache failures, so a transient outage doesn't stick
        self._cache[key] = (now, allowed)
        return allowed
