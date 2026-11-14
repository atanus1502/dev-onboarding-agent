"""Setup guides: a `.onboarding/setup.yaml` in each repo lists the steps to get it running.

The agent can't run anything on a new hire's laptop. Instead each step has a verify command;
the new hire runs it and pastes the output, and `check_output` decides deterministically
whether the step passed, so the model never grades setup on its own judgment.

    title: Run payments-api locally
    steps:
      - id: python
        title: Install Python 3.12
        run: brew install python@3.12
        verify:
          command: python3.12 --version
          expect: '^Python 3\\.12\\.'
        on_failure: If brew is missing, install it from https://brew.sh first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

GUIDE_PATH = ".onboarding/setup.yaml"


@dataclass(frozen=True)
class SetupStep:
    id: str
    title: str
    run: str | None
    instructions: str | None
    verify_command: str
    expect: str
    on_failure: str | None = None


@dataclass(frozen=True)
class SetupGuide:
    repo: str
    title: str
    steps: tuple[SetupStep, ...]

    def step(self, step_id: str) -> SetupStep | None:
        return next((s for s in self.steps if s.id == step_id), None)


@dataclass(frozen=True)
class StepResult:
    passed: bool
    detail: str


def parse_guide(repo: str, text: str) -> SetupGuide:
    raw: dict[str, Any] = yaml.safe_load(text) or {}
    steps = []
    for s in raw.get("steps", []):
        verify = s.get("verify") or {}
        if "command" not in verify or "expect" not in verify:
            raise ValueError(f"step {s.get('id')!r} needs verify.command and verify.expect")
        re.compile(verify["expect"])  # reject a bad pattern at load time, not mid-walkthrough
        steps.append(
            SetupStep(
                id=str(s["id"]),
                title=s["title"],
                run=s.get("run"),
                instructions=s.get("instructions"),
                verify_command=verify["command"],
                expect=verify["expect"],
                on_failure=s.get("on_failure"),
            )
        )
    return SetupGuide(repo=repo, title=raw.get("title", repo), steps=tuple(steps))


def extract_pasted_output(message: str) -> str:
    """Pull the pasted terminal output out of a Slack message, preferring a code block."""
    blocks = re.findall(r"```(?:[\w-]*\n)?(.*?)```", message, flags=re.DOTALL)
    return "\n".join(blocks).strip() if blocks else message.strip()


def check_output(step: SetupStep, pasted: str) -> StepResult:
    output = extract_pasted_output(pasted)
    if not output:
        return StepResult(False, f"No output found. Run `{step.verify_command}` and paste it.")
    if re.search(step.expect, output, flags=re.MULTILINE):
        return StepResult(True, f"Step '{step.title}' verified.")
    hint = f" {step.on_failure}" if step.on_failure else ""
    return StepResult(
        False,
        f"Output didn't match the expected pattern /{step.expect}/ for '{step.title}'.{hint}",
    )
