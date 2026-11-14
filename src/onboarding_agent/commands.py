"""`/onboarding` slash command.

Replies are ephemeral (only the person who typed the command sees them), and every subcommand
answers with the caller's own access, the same way the agent's tools do. Nothing here calls
the model, so commands are instant and free.
"""

from __future__ import annotations

from collections.abc import Callable

from onboarding_agent.agent import UNLINKED_REPLY
from onboarding_agent.config import RepoSource, Settings
from onboarding_agent.identity import IdentityResolver
from onboarding_agent.permissions import PermissionChecker
from onboarding_agent.setup_guides import GUIDE_PATH, parse_guide
from onboarding_agent.sources.github import GitHubClient, GitHubError
from onboarding_agent.store import Store
from onboarding_agent.tools import NO_ACCESS

# Runs the health check, sending each output line to `emit`, and returns its exit code.
DoctorRunner = Callable[[str, Callable[[str], None]], int]

HELP = """*Onboarding agent commands* (only you see these replies)
• `/onboarding repos`: repos and wiki spaces you can access
• `/onboarding setup <repo>`: a repo's setup guide and your next step
• `/onboarding progress`: setup steps you've completed
• `/onboarding doctor`: health check (admins only)

To ask a question, DM me or @mention me in a channel I've been added to."""


class Commands:
    def __init__(
        self,
        settings: Settings,
        identities: IdentityResolver,
        permissions: PermissionChecker,
        github: GitHubClient,
        store: Store,
        doctor: DoctorRunner,
    ) -> None:
        self._settings = settings
        self._identities = identities
        self._permissions = permissions
        self._github = github
        self._store = store
        self._doctor = doctor

    def handle(self, user_id: str, text: str) -> str:
        sub, _, arg = text.strip().partition(" ")
        sub, arg = sub.lower(), arg.strip()
        if sub in ("", "help"):
            return HELP
        if sub == "doctor":
            return self._run_doctor(user_id)
        if not self._identities.resolve(user_id).linked:
            return UNLINKED_REPLY
        if sub == "repos":
            return self._repos(user_id)
        if sub == "setup":
            return self._setup(user_id, arg)
        if sub == "progress":
            return self._progress(user_id)
        return f"I don't know `{sub}`.\n\n{HELP}"

    def _readable(self, user_id: str) -> list[RepoSource]:
        identity = self._identities.resolve(user_id)
        return [
            r
            for r in self._settings.repos
            if self._permissions.can_read_repo(identity, r.full_name)
        ]

    def _repos(self, user_id: str) -> str:
        repos = self._readable(user_id)
        if not repos:
            return "You can't read any of the repos I cover yet. Ask for access in GitHub."
        lines = [f"*Repos you can read ({len(repos)}):*"]
        lines += [
            f"• <{self._github.web_url}/{r.full_name}|{r.full_name}> (`{r.default_branch}`)"
            for r in repos
        ]
        identity = self._identities.resolve(user_id)
        if self._settings.confluence_base_url and identity.atlassian_account_id:
            keys = ", ".join(f"`{s.key}`" for s in self._settings.spaces)
            lines.append(f"\n*Wiki spaces:* {keys} (I check your access page by page)")
        return "\n".join(lines)

    def _resolve_repo(self, user_id: str, name: str) -> RepoSource | None:
        """Accept `owner/name` or just `name`, among repos this user can read."""
        name = name.strip().strip("`").lower()
        for r in self._readable(user_id):
            if name in (r.full_name.lower(), r.full_name.lower().split("/", 1)[1]):
                return r
        return None

    def _setup(self, user_id: str, name: str) -> str:
        if not name:
            return "Which repo? Try `/onboarding setup <repo>`; `/onboarding repos` lists them."
        repo = self._resolve_repo(user_id, name)
        if repo is None:
            return NO_ACCESS
        try:
            f = self._github.get_file(repo.full_name, GUIDE_PATH, repo.default_branch)
        except GitHubError:
            return (
                f"`{repo.full_name}` has no `{GUIDE_PATH}` yet. DM me "
                f"`set up {repo.full_name}` and I'll walk you through its README instead."
            )
        guide = parse_guide(repo.full_name, f.text)
        done = self._store.passed_steps(user_id, repo.full_name)
        lines = [f"*{guide.title}* ({len(done & {s.id for s in guide.steps})}/{len(guide.steps)})"]
        lines += [f"{'✅' if s.id in done else '◻️'} {s.title}" for s in guide.steps]
        nxt = next((s for s in guide.steps if s.id not in done), None)
        if nxt is None:
            lines.append("\nAll steps are done. 🎉")
            return "\n".join(lines)
        lines.append(f"\n*Next: {nxt.title}*")
        if nxt.instructions:
            lines.append(nxt.instructions)
        if nxt.run:
            lines.append(f"Run: `{nxt.run}`")
        lines.append(
            f"Then run `{nxt.verify_command}` and DM me what it prints, saying it's for "
            f"`{repo.full_name}`. I'll check it and move you on."
        )
        return "\n".join(lines)

    def _progress(self, user_id: str) -> str:
        progress = self._store.progress(user_id)
        if not progress:
            return "You haven't completed any setup steps yet. Try `/onboarding setup <repo>`."
        lines = ["*Your setup progress:*"]
        lines += [f"• `{repo}`: {', '.join(sorted(steps))}" for repo, steps in progress.items()]
        return "\n".join(lines)

    def _run_doctor(self, user_id: str) -> str:
        if user_id not in self._settings.admins:
            return "`/onboarding doctor` is for admins only."
        out: list[str] = []
        code = self._doctor(user_id, out.append)
        status = "all checks passed" if code == 0 else "some checks failed"
        return f"*Health check: {status}*\n```\n" + "\n".join(out).strip() + "\n```"
