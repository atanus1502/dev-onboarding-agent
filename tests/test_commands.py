from collections.abc import Callable
from pathlib import Path

from onboarding_agent.commands import HELP, Commands
from onboarding_agent.identity import StaticIdentityResolver
from onboarding_agent.store import Store
from onboarding_agent.tools import NO_ACCESS
from tests.onboarding_fakes import GUIDE, FakeGitHub, FakePermissions, settings

USERS = {
    "U1": {"github": "newhire", "atlassian": "acct-1"},
    "UADMIN": {"github": "lead"},
}


def make(tmp_path: Path, doctor_calls: list[str] | None = None) -> tuple[Commands, Store]:
    store = Store(tmp_path / "db.sqlite")
    calls = doctor_calls if doctor_calls is not None else []

    def doctor(user: str, emit: Callable[[str], None]) -> int:
        calls.append(user)
        emit("ok    settings")
        return 0

    commands = Commands(
        settings=settings(tmp_path),
        identities=StaticIdentityResolver(USERS),
        permissions=FakePermissions(repos={"acme/api", "acme/handbook"}, pages=set()),
        github=FakeGitHub({("acme/api", ".onboarding/setup.yaml"): GUIDE}),  # type: ignore[arg-type]
        store=store,
        doctor=doctor,
    )
    return commands, store


def test_help_is_the_default(tmp_path: Path) -> None:
    commands, _ = make(tmp_path)
    assert commands.handle("U1", "") == HELP
    assert commands.handle("U1", "help") == HELP
    assert "I don't know `deploy`" in commands.handle("U1", "deploy")


def test_unlinked_user_gets_nothing(tmp_path: Path) -> None:
    commands, _ = make(tmp_path)
    for text in ("repos", "setup api", "progress"):
        assert "linked" in commands.handle("U_NOBODY", text)


def test_repos_lists_only_readable_repos(tmp_path: Path) -> None:
    commands, _ = make(tmp_path)
    out = commands.handle("U1", "repos")
    assert "acme/api" in out and "acme/handbook" in out
    assert "acme/secret" not in out
    assert "`ENG`" in out  # Confluence configured and account linked


def test_setup_shows_next_step_then_completion(tmp_path: Path) -> None:
    commands, store = make(tmp_path)
    out = commands.handle("U1", "setup api")  # short name resolves to acme/api
    assert "(0/1)" in out and "Next: Install Python 3.12" in out
    assert "`python3.12 --version`" in out
    store.record_step("U1", "acme/api", "python", True, "Python 3.12.4")
    out = commands.handle("U1", "setup acme/api")
    assert "✅ Install Python 3.12" in out and "All steps are done" in out


def test_setup_denies_unreadable_or_unknown_repos_identically(tmp_path: Path) -> None:
    commands, _ = make(tmp_path)
    assert commands.handle("U1", "setup secret") == NO_ACCESS
    assert commands.handle("U1", "setup does-not-exist") == NO_ACCESS


def test_setup_without_guide_points_to_the_readme(tmp_path: Path) -> None:
    commands, _ = make(tmp_path)
    assert "has no `.onboarding/setup.yaml`" in commands.handle("U1", "setup handbook")


def test_progress(tmp_path: Path) -> None:
    commands, store = make(tmp_path)
    assert "haven't completed" in commands.handle("U1", "progress")
    store.record_step("U1", "acme/api", "python", True, "ok")
    store.record_step("U1", "acme/api", "deps", False, "err")
    assert "`acme/api`: python" in commands.handle("U1", "progress")


def test_doctor_is_admin_only(tmp_path: Path) -> None:
    calls: list[str] = []
    commands, _ = make(tmp_path, calls)
    assert "admins only" in commands.handle("U1", "doctor")
    assert calls == []
    out = commands.handle("UADMIN", "doctor")
    assert calls == ["UADMIN"] and "all checks passed" in out and "ok    settings" in out
