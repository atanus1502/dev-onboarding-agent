# dev-onboarding-agent

Python 3.12+ package managed with **uv**, `src/` layout. CLI entry point: `onboarding-agent` → `onboarding_agent.cli:main`.

A Slack onboarding agent in `src/onboarding_agent/` (Claude tool runner over GitHub +
Confluence, per-user permissions, verified setup walkthroughs, weekly evals).
Design and deploy notes: [docs/onboarding-agent.md](docs/onboarding-agent.md).

## Commands
- Install: `uv sync`
- Test: `uv run pytest` (single test: `uv run pytest tests/test_onboarding_tools.py::test_read_file_returns_permalink_and_records_source`)
- Lint/format: `uv run ruff check --fix . && uv run ruff format .`
- Type check: `uv run mypy src`
- Agent: `uv run onboarding-agent {doctor,serve,ask,eval,mine}` (needs `.env` + `config/*.yaml`, see `.env.example`; run `doctor` first, it costs no tokens)
- Add a dependency: `uv add <pkg>` (dev: `uv add --dev <pkg>`) — don't edit dependency lists by hand or use pip.

## Conventions
- Code lives in `src/onboarding_agent/`; tests in `tests/` as `test_*.py`. Onboarding tests use the in-memory fakes in `tests/onboarding_fakes.py`. Never hit real APIs in tests.
- Full type hints everywhere (mypy strict). Line length 100.
- Run tests, ruff, and mypy before calling a change done.

## Onboarding agent invariants (don't break these)
- Every tool in `src/onboarding_agent/tools.py` checks the asking user's access via `PermissionChecker` before touching a source, and records what it returned with `ctx.note(...)`. That record drives the channel-visibility rule and the eval leak/grounding checks.
- Permission checks fail closed. Missing and forbidden resources return the same `NO_ACCESS` text.
- `check_setup_step` grades `ctx.latest_user_message`, never model-supplied text.
- Model defaults to `claude-opus-5-5`, with `fallbacks="default"` (beta `server-side-fallback-2026-07-01`). Handle `stop_reason == "refusal"` before reading content.

## Notes
- This folder is on Google Drive sync; `.venv/` and caches are gitignored but Drive may still sync them.
