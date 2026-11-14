"""Weekly eval: replay real new-hire questions through the live agent and grade the answers.

Each case runs as the Slack user in `asked_by`, so permissions are exercised for real. Grading:
  - leak (hard fail): the agent read a source matching `must_not_access`
  - grounded: every link in the answer is one a tool actually returned
  - citations: each `must_cite` substring appears in some linked URL
  - facts: a Claude judge checks the answer states each expected fact and contradicts none
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import anthropic
import yaml
from pydantic import BaseModel

from onboarding_agent.agent import Answer, OnboardingAgent

LINK_RE = re.compile(r"<(https?://[^|>\s]+)(?:\|[^>]*)?>|(https?://[^\s<>)]+)")

JUDGE_PROMPT = """\
You are grading an onboarding assistant's answer to a new hire's question against facts that \
an engineer wrote down as the correct answer.

Question:
{question}

Expected facts:
{facts}

Answer:
{answer}

For each expected fact, decide whether the answer states it (paraphrase is fine). Then decide \
whether the answer says anything that contradicts the expected facts. Judge only against the \
facts listed, not your own knowledge of how such systems usually work."""


class FactCheck(BaseModel):
    fact: str
    present: bool


class JudgeVerdict(BaseModel):
    facts: list[FactCheck]
    contradicts: bool
    reasoning: str


@dataclass(frozen=True)
class EvalCase:
    id: str
    asked_by: str
    question: str
    facts: list[str]
    must_cite: list[str] = field(default_factory=list)
    must_not_access: list[str] = field(default_factory=list)
    source: str = ""


@dataclass
class CaseResult:
    id: str
    passed: bool
    leak: list[str]
    ungrounded_links: list[str]
    missing_citations: list[str]
    facts_present: float
    contradicts: bool
    judge_reasoning: str
    answer: str
    seconds: float


def load_cases(path: Path) -> list[EvalCase]:
    return [EvalCase(**c) for c in yaml.safe_load(path.read_text()) or []]


def _strip_fragment(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/")


def answer_links(text: str) -> list[str]:
    return [a or b for a, b in LINK_RE.findall(text)]


def deterministic_checks(case: EvalCase, answer: Answer) -> tuple[list[str], list[str], list[str]]:
    accessed = [s.url for s in answer.sources]
    leak = [p for p in case.must_not_access if any(p in u for u in accessed)]
    links = answer_links(answer.text)
    known = {_strip_fragment(u) for u in accessed}
    ungrounded = [u for u in links if _strip_fragment(u) not in known]
    missing = [p for p in case.must_cite if not any(p in u for u in links)]
    return leak, ungrounded, missing


def judge(client: anthropic.Anthropic, model: str, case: EvalCase, answer: str) -> JudgeVerdict:
    if not case.facts:
        return JudgeVerdict(facts=[], contradicts=False, reasoning="no facts to check")
    response = client.messages.parse(
        model=model,
        max_tokens=4000,
        output_config={"effort": "medium"},
        messages=[
            {
                "role": "user",
                "content": JUDGE_PROMPT.format(
                    question=case.question,
                    facts="\n".join(f"- {f}" for f in case.facts),
                    answer=answer,
                ),
            }
        ],
        output_format=JudgeVerdict,
    )
    if response.parsed_output is None:
        raise RuntimeError(f"judge returned no verdict for {case.id} ({response.stop_reason})")
    return response.parsed_output


def run_case(
    agent: OnboardingAgent, client: anthropic.Anthropic, judge_model: str, case: EvalCase
) -> CaseResult:
    start = time.monotonic()
    answer = agent.answer(case.asked_by, case.question)
    elapsed = time.monotonic() - start
    leak, ungrounded, missing = deterministic_checks(case, answer)
    verdict = judge(client, judge_model, case, answer.text)
    present = sum(f.present for f in verdict.facts) / len(verdict.facts) if verdict.facts else 1.0
    return CaseResult(
        id=case.id,
        passed=not leak
        and not ungrounded
        and not missing
        and present == 1.0
        and not verdict.contradicts,
        leak=leak,
        ungrounded_links=ungrounded,
        missing_citations=missing,
        facts_present=present,
        contradicts=verdict.contradicts,
        judge_reasoning=verdict.reasoning,
        answer=answer.text,
        seconds=round(elapsed, 1),
    )


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    n = len(results) or 1
    return {
        "cases": len(results),
        "pass_rate": round(sum(r.passed for r in results) / n, 3),
        "leaks": sum(bool(r.leak) for r in results),
        "ungrounded": sum(bool(r.ungrounded_links) for r in results),
        "mean_facts_present": round(sum(r.facts_present for r in results) / n, 3),
    }


def compare(
    summary: dict[str, Any], baseline: dict[str, Any] | None, tolerance: float
) -> list[str]:
    """Reasons this run should fail CI."""
    problems = []
    if summary["leaks"]:
        problems.append(f"{summary['leaks']} case(s) read a source the asker must not access")
    if baseline and summary["pass_rate"] < baseline["pass_rate"] - tolerance:
        problems.append(
            f"pass rate {summary['pass_rate']:.0%} dropped from baseline "
            f"{baseline['pass_rate']:.0%}"
        )
    return problems


def render_markdown(summary: dict[str, Any], results: list[CaseResult], problems: list[str]) -> str:
    lines = [
        f"*Onboarding agent eval* — {summary['cases']} questions, pass rate "
        f"{summary['pass_rate']:.0%}, leaks {summary['leaks']}, ungrounded links "
        f"{summary['ungrounded']}",
    ]
    lines += [f":warning: {p}" for p in problems]
    for r in results:
        if r.passed:
            continue
        why = []
        if r.leak:
            why.append(f"LEAK {r.leak}")
        if r.ungrounded_links:
            why.append(f"ungrounded {len(r.ungrounded_links)}")
        if r.missing_citations:
            why.append(f"missing cites {r.missing_citations}")
        if r.facts_present < 1:
            why.append(f"facts {r.facts_present:.0%}")
        if r.contradicts:
            why.append("contradicts")
        lines.append(f"• `{r.id}`: {', '.join(why)}")
    return "\n".join(lines)


def run(
    agent: OnboardingAgent,
    cases: list[EvalCase],
    out_dir: Path,
    judge_model: str,
    baseline_path: Path | None = None,
    tolerance: float = 0.05,
) -> tuple[dict[str, Any], list[str], str]:
    client = anthropic.Anthropic()
    results = [run_case(agent, client, judge_model, c) for c in cases]
    summary = summarize(results)
    baseline = (
        json.loads(baseline_path.read_text()) if baseline_path and baseline_path.exists() else None
    )
    problems = compare(summary, baseline, tolerance)
    report = render_markdown(summary, results, problems)

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    (out_dir / f"{stamp}.json").write_text(
        json.dumps({"summary": summary, "results": [asdict(r) for r in results]}, indent=1)
    )
    (out_dir / f"{stamp}.md").write_text(report)
    return summary, problems, report
