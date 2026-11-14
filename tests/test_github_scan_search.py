import io
import tarfile

import httpx

from onboarding_agent.sources.github import GitHubClient, _parse_query

FILES = {
    "triage/permissions.py": "class ReadOnlyTransport:\n    pass\n",
    "docs/adr/001.md": "# ADR 1\nUse the Anthropic tool runner.\n",
    "README.md": "Incident triage agent using anthropic.\n",
    "logo.png": "\x89PNG\x00\x00binary",
}


def _tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, text in FILES.items():
            data = text.encode("latin-1")
            info = tarfile.TarInfo(f"acme-api-abc123/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def make_client() -> tuple[GitHubClient, list[str]]:
    calls: list[str] = []
    archive = _tarball()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/search/code":
            return httpx.Response(200, json={"items": []})  # repo not in GitHub's index
        if "/commits/" in request.url.path:
            return httpx.Response(200, text="abc123")
        if "/tarball/" in request.url.path:
            return httpx.Response(200, content=archive)
        return httpx.Response(404)

    return GitHubClient("t", http=httpx.Client(transport=httpx.MockTransport(handler))), calls


def test_empty_index_falls_back_to_scanning_the_repo() -> None:
    gh, _ = make_client()
    hits = gh.search_code("ReadOnlyTransport", ["acme/api"])
    assert [h.path for h in hits] == ["triage/permissions.py"]
    assert hits[0].url == "https://github.com/acme/api/blob/abc123/triage/permissions.py#L1-L1"
    assert hits[0].fragments == ("L1: class ReadOnlyTransport:",)


def test_scan_matches_all_terms_case_insensitively_and_skips_binaries() -> None:
    gh, _ = make_client()
    assert {h.path for h in gh.scan_search("ANTHROPIC", ["acme/api"])} == {
        "docs/adr/001.md",
        "README.md",
    }
    assert gh.scan_search("anthropic runner", ["acme/api"])[0].path == "docs/adr/001.md"
    assert gh.scan_search("PNG", ["acme/api"]) == []


def test_path_qualifier_and_snapshot_cache() -> None:
    gh, calls = make_client()
    assert [h.path for h in gh.scan_search("anthropic path:docs/", ["acme/api"])] == [
        "docs/adr/001.md"
    ]
    gh.scan_search("readme", ["acme/api"])
    assert sum("/tarball/" in c for c in calls) == 1  # same commit, downloaded once


def test_parse_query() -> None:
    assert _parse_query('retry "dead letter" path:/src repo:a/b AND') == (
        ["retry", "dead letter"],
        ["src"],
    )


def test_unparseable_query_falls_back_to_scan() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/code":
            return httpx.Response(422, json={"message": "Validation Failed"})
        if "/commits/" in request.url.path:
            return httpx.Response(200, text="abc123")
        return httpx.Response(200, content=_tarball())

    gh = GitHubClient("t", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [h.path for h in gh.search_code("ReadOnlyTransport path:", ["acme/api"])] == [
        "triage/permissions.py"
    ]
