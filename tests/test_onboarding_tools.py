import json
from pathlib import Path
from typing import Any

import pytest

from developer_tools.onboarding.identity import Identity
from developer_tools.onboarding.sources.confluence import WikiPage
from developer_tools.onboarding.store import Store
from developer_tools.onboarding.tools import NO_ACCESS, AskContext, build_tools
from tests.onboarding_fakes import GUIDE, FakeConfluence, FakeGitHub, FakePermissions, settings


def make_ctx(tmp_path: Path, message: str = "hi") -> AskContext:
    github = FakeGitHub(
        {
            ("acme/api", "README.md"): "line1\nline2\nline3",
            ("acme/api", ".onboarding/setup.yaml"): GUIDE,
            ("acme/secret", "keys.txt"): "hunter2",
        }
    )
    confluence = FakeConfluence(
        {
            "1": WikiPage(
                "1",
                "Onboarding",
                "ENG",
                "https://acme.atlassian.net/wiki/1",
                3,
                "Welcome",
                children=(("3", "ADR-001"), ("2", "Comp bands")),
            ),
            "2": WikiPage(
                "2", "Comp bands", "PAY", "https://acme.atlassian.net/wiki/2", 1, "secret"
            ),
        }
    )
    return AskContext(
        identity=Identity("U1", "newhire", "acct-1"),
        latest_user_message=message,
        settings=settings(tmp_path),
        permissions=FakePermissions(repos={"acme/api"}, pages={"1"}),
        github=github,  # type: ignore[arg-type]
        confluence=confluence,  # type: ignore[arg-type]
        store=Store(tmp_path / "db.sqlite"),
    )


def call(ctx: AskContext, name: str, **kwargs: Any) -> str:
    tool = next(t for t in build_tools(ctx) if t.name == name)
    return str(tool.call(kwargs))


def test_read_file_returns_permalink_and_records_source(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    out = call(ctx, "read_file", repo="acme/api", path="README.md", start_line=2)
    assert "blob/abc123/README.md#L2-L3" in out
    assert "line2" in out and "line1" not in out
    assert [s.url for s in ctx.accessed] == [
        "https://github.com/acme/api/blob/abc123/README.md#L2-L3"
    ]


@pytest.mark.parametrize("repo", ["acme/secret", "acme/not-configured"])
def test_unreadable_or_unknown_repo_gives_same_denial(tmp_path: Path, repo: str) -> None:
    ctx = make_ctx(tmp_path)
    assert call(ctx, "read_file", repo=repo, path="keys.txt") == NO_ACCESS
    assert call(ctx, "search_code", query="x", repo=repo) == NO_ACCESS
    assert ctx.accessed == []


def test_search_code_only_covers_readable_repos(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    call(ctx, "search_code", query="hunter2")
    assert ctx.github.searched == [["acme/api"]]  # type: ignore[attr-defined]


def test_wiki_search_filters_pages_per_user(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    hits = json.loads(call(ctx, "search_wiki", query="anything"))
    assert [h["page_id"] for h in hits] == ["1"]
    assert call(ctx, "read_wiki_page", page_id="2") == NO_ACCESS


def test_setup_step_is_graded_against_user_message_not_model(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, message="here you go:\n```\nPython 3.11.9\n```")
    result = json.loads(call(ctx, "check_setup_step", repo="acme/api", step_id="python"))
    assert result["passed"] is False and "Homebrew" in result["detail"]

    ctx.latest_user_message = "```\nPython 3.12.4\n```"
    assert json.loads(call(ctx, "check_setup_step", repo="acme/api", step_id="python"))["passed"]
    guide = json.loads(call(ctx, "get_setup_guide", repo="acme/api"))
    assert guide["steps"][0]["done"] is True


def test_wiki_tools_absent_without_confluence(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    ctx.confluence = None
    names = {t.name for t in build_tools(ctx)}
    assert "search_wiki" not in names and "read_file" in names


def test_wiki_page_lists_only_readable_children(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    ctx.permissions.pages.add("3")  # type: ignore[attr-defined]
    out = call(ctx, "read_wiki_page", page_id="1")
    assert "ADR-001 (page_id 3)" in out
    assert "Comp bands" not in out  # page 2 is restricted for this user
