"""The onboarding agent: one call to `answer` per Slack message."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaTextBlockParam

from developer_tools.onboarding.config import Settings
from developer_tools.onboarding.identity import IdentityResolver
from developer_tools.onboarding.permissions import PermissionChecker
from developer_tools.onboarding.sources.confluence import ConfluenceClient
from developer_tools.onboarding.sources.github import GitHubClient
from developer_tools.onboarding.store import Store
from developer_tools.onboarding.tools import AskContext, SourceRef, build_tools

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the engineering onboarding assistant in Slack. New hires ask you about the codebase, \
internal tooling, architecture and how to get their local environment running. You answer \
from the company's repositories and Confluence wiki using the tools provided.

Grounding
- Answer questions about the company's systems only from what the tools return. If the tools \
don't turn up an answer, say so and suggest who or where to ask; don't fill gaps from general \
knowledge about how such systems usually work. General programming knowledge is fine when \
it's clearly separate from claims about our code.
- Link every claim about our code or docs to its source. read_file returns permalinks to exact \
lines and wiki tools return page URLs; use those, never a URL you composed yourself. You may \
narrow a permalink's #L range to the lines you're citing (e.g. #L52-L66).
- The tools only show what this user can access. When something comes back as not found or no \
access, tell them plainly and suggest requesting access; don't speculate about what it contains.
- If code and wiki disagree, say so and treat the code as current; mention the wiki page may \
be stale.

Local setup walkthroughs
- When someone wants to set up a repo, load its guide with get_setup_guide and go one step at \
a time, starting from the first step not yet done.
- For each step, give the command to run, then ask them to run the verify_command and paste \
its output. When they paste it, call check_setup_step and act on the result: move on when it \
passes; when it fails, diagnose from their output and the step's hint before retrying.
- Never mark a step done or say it worked without a passing check_setup_step.

Style
- This is Slack: use Slack mrkdwn (*bold*, `code`, ```blocks```, links as <url|label>). No \
Markdown headings or tables.
- Lead with the answer. Keep it short; new hires can ask follow-ups.
"""

REFUSAL_REPLY = (
    "I can't help with that one. If you think that's wrong, ask in #eng-help and a human "
    "will pick it up."
)
UNLINKED_REPLY = (
    "I don't have your GitHub or Confluence account linked yet, so I can't see anything on "
    "your behalf. Ask #eng-help to add you to the onboarding agent's identity map."
)


@dataclass(frozen=True)
class Answer:
    text: str
    sources: list[SourceRef]

    @property
    def restricted(self) -> bool:
        """True if any source consulted isn't readable org-wide, so the reply shouldn't be
        posted where people without that access can read it."""
        return any(not s.org_wide for s in self.sources)


class OnboardingAgent:
    def __init__(
        self,
        client: anthropic.Anthropic,
        settings: Settings,
        identities: IdentityResolver,
        permissions: PermissionChecker,
        github: GitHubClient,
        confluence: ConfluenceClient | None,
        store: Store,
    ) -> None:
        self._client = client
        self._settings = settings
        self._identities = identities
        self._permissions = permissions
        self._github = github
        self._confluence = confluence
        self._store = store

    def answer(
        self, slack_user_id: str, question: str, history: list[BetaMessageParam] | None = None
    ) -> Answer:
        identity = self._identities.resolve(slack_user_id)
        if not identity.linked:
            return Answer(UNLINKED_REPLY, [])

        ctx = AskContext(
            identity=identity,
            latest_user_message=question,
            settings=self._settings,
            permissions=self._permissions,
            github=self._github,
            confluence=self._confluence,
            store=self._store,
        )
        system: list[BetaTextBlockParam] = [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ]
        messages: list[BetaMessageParam] = [
            *(history or []),
            {"role": "user", "content": question},
        ]
        runner = self._client.beta.messages.tool_runner(
            model=self._settings.anthropic_model,
            max_tokens=16000,
            system=system,
            tools=build_tools(ctx),
            messages=messages,
            output_config={"effort": self._settings.effort},
            # On a safety-classifier decline, retry on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            max_iterations=25,
        )
        final = runner.until_done()

        if final.stop_reason == "refusal":
            log.warning("refusal for %s: %s", slack_user_id, final.stop_details)
            return Answer(REFUSAL_REPLY, ctx.accessed)
        text = "".join(b.text for b in final.content if b.type == "text").strip()
        if final.stop_reason == "max_tokens":
            text += "\n\n_(I ran out of room; ask me to continue.)_"
        return Answer(text or "I couldn't put an answer together; try rephrasing?", ctx.accessed)
