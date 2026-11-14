"""`onboarding-agent doctor`: checks configuration and credentials with free, read-only calls
(no model tokens are spent), and optionally shows what one Slack user can access."""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import partial

import anthropic

from onboarding_agent.config import Settings
from onboarding_agent.identity import StaticIdentityResolver
from onboarding_agent.permissions import LivePermissionChecker
from onboarding_agent.runtime import build_confluence, build_github
from onboarding_agent.setup_guides import GUIDE_PATH, parse_guide
from onboarding_agent.sources.github import GitHubError


class Report:
    def __init__(self, emit: Callable[[str], None] = print) -> None:
        self.failures = 0
        self._emit = emit

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.failures += not ok
        self._emit(f"{'ok  ' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
        return ok

    def info(self, text: str) -> None:
        self._emit(f"      {text}")


def _attempt(report: Report, label: str, fn: Callable[[], object]) -> bool:
    try:
        result = fn()
    except Exception as e:
        return report.check(label, False, f"{type(e).__name__}: {e}"[:200])
    return report.check(label, result is not False)


def run_doctor(as_user: str | None = None, emit: Callable[[str], None] = print) -> int:
    report = Report(emit)
    try:
        settings = Settings.from_env()
    except KeyError as e:
        report.check("settings", False, f"missing environment variable {e}")
        return 1
    except FileNotFoundError as e:
        report.check("settings", False, f"missing file {e.filename}")
        return 1
    report.check("settings", True, f"{len(settings.repos)} repos, {len(settings.spaces)} spaces")

    _attempt(
        report,
        f"anthropic: {settings.anthropic_model}",
        lambda: anthropic.Anthropic().models.retrieve(settings.anthropic_model),
    )

    try:
        github = build_github(settings)
        confluence = build_confluence(settings)
    except ValueError as e:
        report.check("credentials", False, str(e))
        return 1

    for repo in settings.repos:
        if not _attempt(
            report,
            f"github: bot can read {repo.full_name}",
            partial(github.repo_reachable, repo.full_name),
        ):
            continue
        try:
            f = github.get_file(repo.full_name, GUIDE_PATH, repo.default_branch)
            guide = parse_guide(repo.full_name, f.text)
            report.info(f"setup guide: {len(guide.steps)} steps")
        except GitHubError:
            report.info(f"no {GUIDE_PATH} (setup walkthroughs fall back to the README)")
        except ValueError as e:
            report.check(f"github: {repo.full_name} setup guide", False, str(e))

    if confluence is None:
        report.info("confluence not configured: answering from GitHub only")
    else:
        for space in settings.spaces:
            _attempt(
                report,
                f"confluence: bot can read space {space.key}",
                partial(confluence.space_reachable, space.key),
            )

    if os.environ.get("SLACK_BOT_TOKEN"):
        from slack_sdk import WebClient

        _attempt(
            report,
            "slack: bot token",
            lambda: WebClient(token=os.environ["SLACK_BOT_TOKEN"]).auth_test(),
        )
    else:
        report.check("slack: bot token", False, "SLACK_BOT_TOKEN not set")
    if not os.environ.get("SLACK_APP_TOKEN"):
        report.check("slack: app token", False, "SLACK_APP_TOKEN not set (needed for serve)")

    identities = StaticIdentityResolver.from_file(settings.identities_path)
    report.check("identities", settings.identities_path.exists(), str(settings.identities_path))

    if as_user:
        identity = identities.resolve(as_user)
        if report.check(
            f"identity {as_user}",
            identity.linked,
            f"github={identity.github_login} atlassian={identity.atlassian_account_id}",
        ):
            perms = LivePermissionChecker(github, confluence)
            for repo in settings.repos:
                can = perms.can_read_repo(identity, repo.full_name)
                report.info(f"{'can' if can else 'cannot'} read {repo.full_name}")

    emit(f"\n{report.failures} problem(s)" if report.failures else "\nall checks passed")
    return 1 if report.failures else 0
