"""Thin GitHub REST client. Authenticates as the bot (GitHub App installation token or PAT);
per-user access is enforced separately in permissions.py."""

from __future__ import annotations

import io
import re
import tarfile
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import httpx

from onboarding_agent.sources.github_auth import TokenProvider, static_token

# GitHub rejects code-search queries longer than 256 characters.
_MAX_QUERY_LEN = 256

# Fallback search downloads each repo once per commit. These caps keep a huge repo from
# stalling a Slack reply or filling memory.
_MAX_ARCHIVE_BYTES = 50_000_000
_MAX_FILE_BYTES = 500_000
_SNAPSHOT_CACHE_SIZE = 20
_FRAGMENTS_PER_FILE = 3


@dataclass(frozen=True)
class CodeHit:
    repo: str
    path: str
    url: str
    fragments: tuple[str, ...]


@dataclass(frozen=True)
class FileContent:
    repo: str
    path: str
    commit_sha: str
    text: str


@dataclass(frozen=True)
class DirEntry:
    name: str
    path: str
    type: str  # "file" | "dir" | "symlink" | "submodule"


class GitHubError(Exception):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str | TokenProvider,
        api_url: str = "https://api.github.com",
        web_url: str = "https://github.com",
        http: httpx.Client | None = None,
    ) -> None:
        self.web_url = web_url.rstrip("/")
        self._http = http or httpx.Client(timeout=20.0)
        self._api = api_url.rstrip("/")
        self._token = static_token(token) if isinstance(token, str) else token
        self._snapshots: OrderedDict[tuple[str, str], dict[str, str]] = OrderedDict()

    def _headers(self, accept: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, path: str, accept: str = "application/vnd.github+json", **params: Any) -> Any:
        resp = self._http.get(f"{self._api}{path}", params=params, headers=self._headers(accept))
        if resp.status_code == 404:
            raise GitHubError(f"not found: {path}")
        resp.raise_for_status()
        # Raw and sha media types come back as text even though GitHub labels the raw one
        # "application/vnd.github.raw+json", so decide by what was asked for, not the header.
        if accept == "application/vnd.github+json" or accept.endswith("text-match+json"):
            return resp.json()
        return resp.text

    def repo_reachable(self, repo: str) -> bool:
        try:
            self._get(f"/repos/{repo}")
        except GitHubError:
            return False
        return True

    def visibility(self, repo: str) -> str:
        """ "public", "private" or "internal" (internal = every member of the enterprise)."""
        return str(self._get(f"/repos/{repo}").get("visibility", "private"))

    def collaborator_permission(self, repo: str, login: str) -> str:
        """Effective permission ("admin", "write", "read" or "none"), including access granted
        through teams and org base permissions."""
        try:
            data = self._get(f"/repos/{repo}/collaborators/{login}/permission")
        except GitHubError:
            return "none"
        return str(data.get("permission", "none"))

    def resolve_commit(self, repo: str, ref: str) -> str:
        return str(self._get(f"/repos/{repo}/commits/{ref}", accept="application/vnd.github.sha"))

    def get_file(self, repo: str, path: str, ref: str) -> FileContent:
        sha = self.resolve_commit(repo, ref).strip()
        text = self._get(
            f"/repos/{repo}/contents/{path.lstrip('/')}",
            accept="application/vnd.github.raw+json",
            ref=sha,
        )
        if not isinstance(text, str):
            raise GitHubError(f"{path} is a directory, not a file")
        return FileContent(repo=repo, path=path.lstrip("/"), commit_sha=sha, text=text)

    def list_dir(self, repo: str, path: str, ref: str) -> list[DirEntry]:
        data = self._get(f"/repos/{repo}/contents/{path.strip('/')}", ref=ref)
        if not isinstance(data, list):
            raise GitHubError(f"{path} is a file, not a directory")
        return [DirEntry(name=e["name"], path=e["path"], type=e["type"]) for e in data]

    def search_code(self, query: str, repos: list[str], limit: int = 10) -> list[CodeHit]:
        """GitHub code search, falling back to scanning the default branch when the index
        returns nothing (GitHub doesn't index every repo, so an empty result proves nothing)."""
        return self._indexed_search(query, repos, limit) or self.scan_search(query, repos, limit)

    def _indexed_search(self, query: str, repos: list[str], limit: int) -> list[CodeHit]:
        hits: list[CodeHit] = []
        for chunk in _chunk_repo_qualifiers(query, repos):
            try:
                data = self._get(
                    "/search/code",
                    accept="application/vnd.github.text-match+json",
                    q=chunk,
                    per_page=limit,
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 422:  # query GitHub can't parse; let the scan try
                    return []
                raise
            for item in data.get("items", []):
                hits.append(
                    CodeHit(
                        repo=item["repository"]["full_name"],
                        path=item["path"],
                        url=item["html_url"],
                        fragments=tuple(m["fragment"] for m in item.get("text_matches", [])),
                    )
                )
            if len(hits) >= limit:
                break
        return hits[:limit]

    def scan_search(self, query: str, repos: list[str], limit: int = 10) -> list[CodeHit]:
        """Case-insensitive search of each repo's default branch: a file matches when every
        search term appears in its path or contents. Qualifiers like `path:docs/` filter paths."""
        terms, path_prefixes = _parse_query(query)
        if not terms and not path_prefixes:
            return []
        hits: list[CodeHit] = []
        for repo in repos:
            sha = self.resolve_commit(repo, "HEAD").strip()
            for path, text in self._snapshot(repo, sha).items():
                if path_prefixes and not any(path.startswith(p) for p in path_prefixes):
                    continue
                haystack = f"{path}\n{text}".lower()
                if not all(t in haystack for t in terms):
                    continue
                lines = [
                    n
                    for n, line in enumerate(text.splitlines(), 1)
                    if any(t in line.lower() for t in terms)
                ][:_FRAGMENTS_PER_FILE]
                first = lines[0] if lines else 1
                hits.append(
                    CodeHit(
                        repo=repo,
                        path=path,
                        url=self.permalink(repo, sha, path, first, first),
                        fragments=tuple(
                            f"L{n}: {text.splitlines()[n - 1].strip()[:200]}" for n in lines
                        ),
                    )
                )
                if len(hits) >= limit:
                    return hits
        return hits

    def _snapshot(self, repo: str, sha: str) -> dict[str, str]:
        key = (repo, sha)
        if key in self._snapshots:
            self._snapshots.move_to_end(key)
            return self._snapshots[key]
        files = self._download_text_files(repo, sha)
        self._snapshots[key] = files
        if len(self._snapshots) > _SNAPSHOT_CACHE_SIZE:
            self._snapshots.popitem(last=False)
        return files

    def _download_text_files(self, repo: str, sha: str) -> dict[str, str]:
        with self._http.stream(
            "GET",
            f"{self._api}/repos/{repo}/tarball/{sha}",
            headers=self._headers("application/vnd.github+json"),
            follow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            buf = io.BytesIO()
            for chunk in resp.iter_bytes():
                buf.write(chunk)
                if buf.tell() > _MAX_ARCHIVE_BYTES:
                    raise GitHubError(f"{repo} is too large to scan")
        buf.seek(0)
        files: dict[str, str] = {}
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            for member in tar:
                if not member.isfile() or member.size > _MAX_FILE_BYTES:
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                data = f.read()
                if b"\0" in data[:8000]:
                    continue  # binary
                # Archive paths start with "<owner>-<repo>-<sha>/".
                path = member.name.split("/", 1)[1] if "/" in member.name else member.name
                files[path] = data.decode("utf-8", errors="replace")
        return files

    def permalink(self, repo: str, commit_sha: str, path: str, start: int, end: int) -> str:
        return f"{self.web_url}/{repo}/blob/{commit_sha}/{path}#L{start}-L{end}"


def _chunk_repo_qualifiers(query: str, repos: list[str]) -> list[str]:
    """Split repo: qualifiers across several queries so each stays under GitHub's length cap."""
    queries: list[str] = []
    current = query
    for repo in repos:
        qualifier = f" repo:{repo}"
        if len(current) + len(qualifier) > _MAX_QUERY_LEN and current != query:
            queries.append(current)
            current = query
        current += qualifier
    if current != query:
        queries.append(current)
    return queries


def _parse_query(query: str) -> tuple[list[str], list[str]]:
    """Split a GitHub-style query into lowercase search terms and `path:` prefixes. Other
    qualifiers (repo:, language:, ...) are ignored by the scan."""
    terms: list[str] = []
    prefixes: list[str] = []
    for token in re.findall(r'"[^"]+"|\S+', query):
        token = token.strip('"')
        if token.lower().startswith("path:"):
            prefixes.append(token[5:].lstrip("/"))
        elif ":" in token and not token.startswith("http"):
            continue
        elif token.upper() not in {"AND", "OR", "NOT"}:
            terms.append(token.lower())
    return terms, prefixes
