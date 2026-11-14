"""Pull recent real questions out of the interaction log as eval candidates.

Candidates still need a human to write `facts` and citation expectations; the agent's own
answer is included only as a starting point for that, never as the expected answer.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import yaml

from developer_tools.onboarding.store import Store


def mine(store: Store, days: int, out: Path) -> int:
    rows = store.interactions_since(time.time() - days * 86400)
    candidates = []
    seen: set[str] = set()
    # Thumbs-down first: those are the questions most worth turning into regression tests.
    for r in sorted(rows, key=lambda r: r.feedback or 0):
        key = re.sub(r"\W+", " ", r.question.lower()).strip()
        if len(key) < 15 or key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "id": f"q{r.id}",
                "asked_by": r.slack_user,
                "question": r.question,
                "facts": [],
                "must_cite": [],
                "must_not_access": [],
                "source": f"slack interaction {r.id}, feedback={r.feedback}",
                "_agent_answer_for_reference": r.answer,
            }
        )
    out.write_text(yaml.safe_dump(candidates, sort_keys=False, allow_unicode=True))
    return len(candidates)
