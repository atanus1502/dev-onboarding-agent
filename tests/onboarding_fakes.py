"""In-memory stand-ins for GitHub, Confluence and permissions."""

from __future__ import annotations

from pathlib import Path

from onboarding_agent.config import RepoSource, Settings, WikiSpace
from onboarding_agent.identity import Identity
from onboarding_agent.sources.confluence import WikiHit, WikiPage
from onboarding_agent.sources.github import CodeHit, DirEntry, FileContent, GitHubError

GUIDE = """
title: Run api locally
steps:
  - id: python
    title: Install Python 3.12
    run: brew install python@3.12
    verify: {command: python3.12 --version, expect: '^Python 3\\.12\\.'}
    on_failure: Install Homebrew first.
"""


class FakeGitHub:
    web_url = "https://github.com"

    def __init__(self, files: dict[tuple[str, str], str]) -> None:
        self.files = files
        self.searched: list[list[str]] = []

    def get_file(self, repo: str, path: str, ref: str) -> FileContent:
        if (repo, path) not in self.files:
            raise GitHubError("not found")
        return FileContent(repo, path, "abc123", self.files[(repo, path)])

    def list_dir(self, repo: str, path: str, ref: str) -> list[DirEntry]:
        return [DirEntry(p.split("/")[-1], p, "file") for (r, p) in self.files if r == repo]

    def search_code(self, query: str, repos: list[str], limit: int = 10) -> list[CodeHit]:
        self.searched.append(repos)
        return [
            CodeHit(r, p, f"https://github.com/{r}/blob/abc123/{p}", (query,))
            for (r, p) in self.files
            if r in repos
        ]

    def permalink(self, repo: str, sha: str, path: str, start: int, end: int) -> str:
        return f"https://github.com/{repo}/blob/{sha}/{path}#L{start}-L{end}"


class FakeConfluence:
    def __init__(self, pages: dict[str, WikiPage]) -> None:
        self.pages = pages

    def search(self, query: str, space_keys: list[str], limit: int = 10) -> list[WikiHit]:
        return [
            WikiHit(p.page_id, p.title, p.space_key, p.url, "...")
            for p in self.pages.values()
            if p.space_key in space_keys
        ]

    def get_page(self, page_id: str) -> WikiPage:
        return self.pages[page_id]


class FakePermissions:
    def __init__(self, repos: set[str], pages: set[str]) -> None:
        self.repos, self.pages = repos, pages

    def can_read_repo(self, identity: Identity, repo: str) -> bool:
        return repo in self.repos

    def can_read_page(self, identity: Identity, page_id: str) -> bool:
        return page_id in self.pages


def settings(tmp: Path) -> Settings:
    return Settings(
        anthropic_model="claude-opus-5-5",
        effort="medium",
        github_token="t",
        github_api_url="https://api.github.com",
        github_web_url="https://github.com",
        confluence_base_url="https://acme.atlassian.net",
        confluence_email="e",
        confluence_api_token="t",
        repos=(
            RepoSource("acme/api"),
            RepoSource("acme/secret"),
            RepoSource("acme/handbook", org_wide=True),
        ),
        spaces=(WikiSpace("ENG", org_wide=True), WikiSpace("PAY")),
        identities_path=tmp / "ids.yaml",
        db_path=tmp / "db.sqlite",
    )
