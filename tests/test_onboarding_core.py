from typing import Any
from unittest.mock import MagicMock

from onboarding_agent.agent import Answer
from onboarding_agent.evals.runner import EvalCase, compare, deterministic_checks
from onboarding_agent.identity import Identity
from onboarding_agent.permissions import LivePermissionChecker
from onboarding_agent.slack_app import PRIVATE_NOTE, post_answer, thread_history
from onboarding_agent.sources.confluence import html_to_text
from onboarding_agent.sources.github import _chunk_repo_qualifiers
from onboarding_agent.tools import SourceRef

NEWHIRE = Identity("U1", "newhire", "acct-1")


def checker(github: Any, confluence: Any = None, clock: Any = None) -> LivePermissionChecker:
    return LivePermissionChecker(
        github, confluence or MagicMock(), ttl=60, clock=clock or (lambda: 0)
    )


def test_permission_fails_closed_on_error_and_unlinked() -> None:
    gh = MagicMock()
    gh.collaborator_permission.side_effect = RuntimeError("github down")
    c = checker(gh)
    assert c.can_read_repo(NEWHIRE, "acme/api") is False
    assert c.can_read_repo(Identity("U2"), "acme/api") is False
    assert c.can_read_page(Identity("U2"), "1") is False


def test_permission_cache_expires() -> None:
    now = [0.0]
    gh = MagicMock()
    gh.collaborator_permission.return_value = "read"
    c = checker(gh, clock=lambda: now[0])
    assert c.can_read_repo(NEWHIRE, "acme/api")
    gh.collaborator_permission.return_value = "none"  # access revoked
    assert c.can_read_repo(NEWHIRE, "acme/api")  # still cached
    now[0] = 61
    assert not c.can_read_repo(NEWHIRE, "acme/api")


def test_repo_qualifiers_are_chunked_under_limit() -> None:
    repos = [f"acme/service-number-{i:03}" for i in range(40)]
    queries = _chunk_repo_qualifiers("DATABASE_URL", repos)
    assert len(queries) > 1 and all(len(q) <= 256 for q in queries)
    assert sum(q.count("repo:") for q in queries) == 40


def test_restricted_answer_goes_ephemeral_in_channels_only() -> None:
    restricted = Answer("x", [SourceRef("code", "u", "t", org_wide=False)])
    client = MagicMock()
    post_answer(client, "C1", "channel", "U1", "1.0", restricted)
    client.chat_postEphemeral.assert_called_once()
    assert client.chat_postMessage.call_args.kwargs["text"] == PRIVATE_NOTE

    client = MagicMock()
    post_answer(client, "D1", "im", "U1", "1.0", restricted)
    client.chat_postEphemeral.assert_not_called()


def test_thread_history_roles() -> None:
    msgs = [
        {"ts": "1", "user": "UBOT", "text": "hello"},
        {"ts": "2", "user": "U1", "text": "<@UBOT> how do I deploy?"},
        {"ts": "3", "user": "UBOT", "text": "Use the pipeline."},
        {"ts": "4", "user": "U1", "text": "current"},
    ]
    h = thread_history(msgs, "UBOT", current_ts="4")
    assert [m["role"] for m in h] == ["user", "assistant"]
    assert h[0]["content"] == "<@U1>: how do I deploy?"


def test_eval_deterministic_checks() -> None:
    case = EvalCase("c", "U1", "q", [], must_cite=["acme/api"], must_not_access=["acme/secret"])
    answer = Answer(
        "See <https://github.com/acme/api/blob/s/a.py#L1-L2|a.py> and https://made.up/x",
        [
            SourceRef("code", "https://github.com/acme/api/blob/s/a.py#L1-L9", "a", False),
            SourceRef("code", "https://github.com/acme/secret/blob/s/k", "k", False),
        ],
    )
    leak, ungrounded, missing = deterministic_checks(case, answer)
    assert leak == ["acme/secret"]
    assert ungrounded == ["https://made.up/x"]
    assert missing == []


def test_compare_flags_leaks_and_regressions() -> None:
    assert compare({"leaks": 0, "pass_rate": 0.9}, {"pass_rate": 0.9}, 0.05) == []
    assert len(compare({"leaks": 1, "pass_rate": 0.8}, {"pass_rate": 0.9}, 0.05)) == 2


def test_html_to_text() -> None:
    assert html_to_text("<h1>Title</h1><ul><li>a</li><li>b</li></ul>") == "Title\n- a\n- b"


def test_public_repo_skips_collaborator_lookup_but_private_does_not() -> None:
    gh = MagicMock()
    gh.visibility.side_effect = lambda repo: "public" if repo == "acme/open" else "private"
    gh.collaborator_permission.return_value = "none"
    c = checker(gh)
    assert c.can_read_repo(NEWHIRE, "acme/open")
    assert not c.can_read_repo(NEWHIRE, "acme/closed")
    gh.collaborator_permission.assert_called_once_with("acme/closed", "newhire")
    assert not c.can_read_repo(Identity("U2"), "acme/open")  # still requires a linked account


def test_get_file_returns_raw_text_despite_json_content_type() -> None:
    import httpx

    from onboarding_agent.sources.github import GitHubClient

    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(
                200, text="abc123", headers={"content-type": "application/vnd.github.sha"}
            )
        return httpx.Response(
            200,
            text="# Title\n{not json",
            headers={"content-type": "application/vnd.github.raw+json"},
        )

    gh = GitHubClient("t", http=httpx.Client(transport=httpx.MockTransport(handler)))
    f = gh.get_file("acme/api", "README.md", "main")
    assert f.text == "# Title\n{not json" and f.commit_sha == "abc123"
