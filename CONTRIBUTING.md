# Contributing

## Setup

```sh
uv sync                                   # Python 3.12+, installs into .venv
cp .env.example .env                      # fill in only what you need
cp config/sources.example.yaml config/sources.yaml
cp config/identities.example.yaml config/identities.yaml
uv run onboarding-agent doctor            # checks config and credentials, costs no tokens
```

Tests never call real APIs, so you can run them with no credentials at all.

## Before opening a PR

```sh
uv run ruff check --fix . && uv run ruff format .
uv run mypy src
uv run pytest
```

CI runs the same checks on every pull request, and also builds the Docker image. To run ruff
automatically on each commit (and block accidentally committed private keys), install the hooks
once with `uvx pre-commit install`.

## Ground rules

- **Permissions are enforced in code.** Every tool in `src/onboarding_agent/tools.py`
  must check the asking user's access with `PermissionChecker` before reading a source, and
  record what it returned with `ctx.note(...)`. Add a test for any new tool's deny path.
- **Fail closed.** If a permission check can't complete, deny.
- **Prompt or model changes need eval numbers.** Run `uv run onboarding-agent eval` and put the
  before/after pass rate in the PR description. It calls the Claude API, so it costs money.
- **Real new-hire questions make the best eval cases.** `uv run onboarding-agent mine` exports
  recent ones; write the expected facts yourself rather than copying the agent's answer.

## Commit messages

Short imperative subject (`Add Confluence child pages to read_wiki_page`), with a body
explaining why when it isn't obvious.
