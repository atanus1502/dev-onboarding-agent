"""Tools the model can call. They are built per question as closures over an AskContext, so
each call runs with the asking user's identity and cannot reach anything that user can't."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from anthropic import beta_tool

from onboarding_agent.config import Settings
from onboarding_agent.identity import Identity
from onboarding_agent.permissions import PermissionChecker
from onboarding_agent.setup_guides import GUIDE_PATH, check_output, parse_guide
from onboarding_agent.sources.confluence import ConfluenceClient
from onboarding_agent.sources.github import GitHubClient, GitHubError
from onboarding_agent.store import Store

log = logging.getLogger(__name__)

MAX_FILE_LINES = 400
MAX_WIKI_CHARS = 30_000

# Same message whether the resource is missing or forbidden, so the agent never confirms that
# a repo or page the user can't see exists.
NO_ACCESS = "Not found, or you don't have access to it with your GitHub/Confluence account."


@dataclass(frozen=True)
class SourceRef:
    kind: str  # "code" | "wiki"
    url: str
    title: str
    org_wide: bool


@dataclass
class AskContext:
    identity: Identity
    latest_user_message: str
    settings: Settings
    permissions: PermissionChecker
    github: GitHubClient
    confluence: ConfluenceClient | None
    store: Store
    accessed: list[SourceRef] = field(default_factory=list)

    def readable_repos(self) -> list[str]:
        return [
            r.full_name
            for r in self.settings.repos
            if self.permissions.can_read_repo(self.identity, r.full_name)
        ]

    def readable_spaces(self) -> list[str]:
        # Space membership isn't checked here: search results are filtered page by page below,
        # which also respects page-level restrictions inside an open space.
        if self.confluence is None or not self.identity.atlassian_account_id:
            return []
        return [s.key for s in self.settings.spaces]

    def note(self, kind: str, url: str, title: str, org_wide: bool) -> None:
        self.accessed.append(SourceRef(kind, url, title, org_wide))


def _dump(value: Any) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False)


def build_tools(ctx: AskContext) -> list[Any]:
    settings = ctx.settings

    def repo_allowed(repo: str) -> bool:
        return settings.repo(repo) is not None and ctx.permissions.can_read_repo(ctx.identity, repo)

    @beta_tool
    def list_repositories() -> str:
        """List the repositories this user can read, with their default branch and the folders
        that hold architecture docs or ADRs."""
        repos = [settings.repo(name) for name in ctx.readable_repos()]
        return _dump(
            [
                {
                    "repo": r.full_name,
                    "url": f"{ctx.github.web_url}/{r.full_name}",
                    "default_branch": r.default_branch,
                    "docs_paths": r.docs_paths,
                }
                for r in repos
                if r is not None
            ]
            or "This user can't read any configured repository."
        )

    @beta_tool
    def search_code(query: str, repo: str | None = None) -> str:
        """Search code and in-repo docs (READMEs, ADRs, docs folders) using GitHub code search.

        Args:
            query: GitHub code-search terms, e.g. "DATABASE_URL" or "retry path:docs/".
            repo: Optional "owner/name" to limit the search to one repository.
        """
        if repo is not None and not repo_allowed(repo):
            return NO_ACCESS
        repos = [repo] if repo else ctx.readable_repos()
        if not repos:
            return "This user can't read any configured repository."
        hits = ctx.github.search_code(query, repos)
        for h in hits:
            source = settings.repo(h.repo)
            ctx.note("code", h.url, f"{h.repo}/{h.path}", source.org_wide if source else False)
        return _dump(
            [{"repo": h.repo, "path": h.path, "url": h.url, "matches": h.fragments} for h in hits]
            or "No results."
        )

    @beta_tool
    def read_file(repo: str, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Read a file from a repository's default branch, with line numbers and a permalink to
        the exact lines returned. Long files come back a page at a time.

        Args:
            repo: "owner/name".
            path: File path within the repository.
            start_line: First line to return (1-based).
            end_line: Last line to return; defaults to start_line + 399.
        """
        source = settings.repo(repo)
        if source is None or not repo_allowed(repo):
            return NO_ACCESS
        try:
            f = ctx.github.get_file(repo, path, source.default_branch)
        except GitHubError:
            return f"No file at {path} in {repo}."
        lines = f.text.splitlines()
        start = max(start_line, 1)
        end = min(end_line or start + MAX_FILE_LINES - 1, start + MAX_FILE_LINES - 1, len(lines))
        url = ctx.github.permalink(repo, f.commit_sha, f.path, start, end)
        ctx.note("code", url, f"{repo}/{f.path}", source.org_wide)
        body = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))
        more = (
            f"\n[{len(lines) - end} more lines; continue at start_line={end + 1}]"
            if end < len(lines)
            else ""
        )
        return f"permalink: {url}\n{body}{more}"

    @beta_tool
    def list_directory(repo: str, path: str = "") -> str:
        """List the files and folders at a path in a repository.

        Args:
            repo: "owner/name".
            path: Folder path; empty for the repository root.
        """
        source = settings.repo(repo)
        if source is None or not repo_allowed(repo):
            return NO_ACCESS
        try:
            entries = ctx.github.list_dir(repo, path, source.default_branch)
        except GitHubError as e:
            return str(e)
        return _dump([{"path": e.path, "type": e.type} for e in entries])

    @beta_tool
    def search_wiki(query: str) -> str:
        """Search the internal Confluence wiki (onboarding guides, runbooks, architecture pages).

        Args:
            query: Free-text search terms.
        """
        spaces = ctx.readable_spaces()
        if not spaces:
            return "This user's Confluence account isn't linked, so the wiki is unavailable."
        assert ctx.confluence is not None  # readable_spaces() is empty without it
        hits = ctx.confluence.search(query, spaces, limit=15)
        visible = [h for h in hits if ctx.permissions.can_read_page(ctx.identity, h.page_id)][:10]
        for h in visible:
            space = settings.space(h.space_key)
            ctx.note("wiki", h.url, h.title, space.org_wide if space else False)
        return _dump(
            [
                {
                    "page_id": h.page_id,
                    "title": h.title,
                    "space": h.space_key,
                    "url": h.url,
                    "excerpt": h.excerpt,
                }
                for h in visible
            ]
            or "No results."
        )

    @beta_tool
    def read_wiki_page(page_id: str) -> str:
        """Read a Confluence page's full text.

        Args:
            page_id: The page id from search_wiki.
        """
        if ctx.confluence is None or not ctx.permissions.can_read_page(ctx.identity, page_id):
            return NO_ACCESS
        try:
            page = ctx.confluence.get_page(page_id)
        except Exception:
            log.exception("failed to fetch page %s", page_id)
            return NO_ACCESS
        space = settings.space(page.space_key)
        if space is None:
            return NO_ACCESS  # outside the spaces this agent is configured to answer from
        ctx.note("wiki", page.url, page.title, space.org_wide)
        text = page.text
        if len(text) > MAX_WIKI_CHARS:
            text = text[:MAX_WIKI_CHARS] + "\n[page continues; quote the link for the rest]"
        # Only list children this user can read, so titles of restricted pages don't leak.
        children = [
            f"- {title} (page_id {cid})"
            for cid, title in page.children
            if ctx.permissions.can_read_page(ctx.identity, cid)
        ]
        child_list = "\n\nchild pages:\n" + "\n".join(children) if children else ""
        return (
            f"title: {page.title}\nurl: {page.url}\nversion: {page.version}\n\n{text}{child_list}"
        )

    @beta_tool
    def get_setup_guide(repo: str) -> str:
        """Load a repository's local-environment setup guide and this user's progress through it.

        Args:
            repo: "owner/name".
        """
        source = settings.repo(repo)
        if source is None or not repo_allowed(repo):
            return NO_ACCESS
        try:
            f = ctx.github.get_file(repo, GUIDE_PATH, source.default_branch)
        except GitHubError:
            return f"{repo} has no {GUIDE_PATH}; fall back to its README."
        guide = parse_guide(repo, f.text)
        url = ctx.github.permalink(repo, f.commit_sha, GUIDE_PATH, 1, len(f.text.splitlines()))
        ctx.note("code", url, f"{repo} setup guide", source.org_wide)
        done = ctx.store.passed_steps(ctx.identity.slack_user_id, repo)
        return _dump(
            {
                "title": guide.title,
                "url": url,
                "steps": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "run": s.run,
                        "instructions": s.instructions,
                        "verify_command": s.verify_command,
                        "done": s.id in done,
                    }
                    for s in guide.steps
                ],
            }
        )

    @beta_tool
    def check_setup_step(repo: str, step_id: str) -> str:
        """Verify a setup step against the output the user pasted in their latest message. Call
        this after the user has run the step's verify_command and pasted what it printed.

        Args:
            repo: "owner/name".
            step_id: The step id from get_setup_guide.
        """
        source = settings.repo(repo)
        if source is None or not repo_allowed(repo):
            return NO_ACCESS
        try:
            f = ctx.github.get_file(repo, GUIDE_PATH, source.default_branch)
        except GitHubError:
            return f"{repo} has no {GUIDE_PATH}."
        step = parse_guide(repo, f.text).step(step_id)
        if step is None:
            return f"No step {step_id!r} in the {repo} guide."
        # Graded against the user's actual message, never against text the model supplies.
        result = check_output(step, ctx.latest_user_message)
        ctx.store.record_step(
            ctx.identity.slack_user_id, repo, step_id, result.passed, ctx.latest_user_message
        )
        return _dump({"passed": result.passed, "detail": result.detail})

    wiki = [search_wiki, read_wiki_page] if ctx.confluence is not None else []
    return [
        list_repositories,
        search_code,
        read_file,
        list_directory,
        *wiki,
        get_setup_guide,
        check_setup_step,
    ]
