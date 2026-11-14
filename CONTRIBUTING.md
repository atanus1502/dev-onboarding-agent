# Contributing

## Setup

```sh
uv sync                                   # Python 3.12+, installs into .venv
cp .env.example .env                      # fill in only what you need
cp config/sources.example.yaml config/sources.yaml
cp config/identities.example.yaml config/identities.yaml
uv run devtools onboard doctor            # checks config and credentials, costs no tokens
```

Tests never call real APIs, so you can run them with no credentials at all.

## Before opening a PR

```sh
uv run ruff check --fix . && uv run ruff format .
uv run mypy src
uv run pytest
```

CI runs the same three checks on every pull request.

## Ground rules

- **Permissions are enforced in code.** Every tool in `src/developer_tools/onboarding/tools.py`
  must check the asking user's access with `PermissionChecker` before reading a source, and
  record what it returned with `ctx.note(...)`. Add a test for any new tool's deny path.
- **Fail closed.** If a permission check can't complete, deny.
- **Prompt or model changes need eval numbers.** Run `uv run devtools onboard eval` and put the
  before/after pass rate in the PR description. It calls the Claude API, so it costs money.
- **Real new-hire questions make the best eval cases.** `uv run devtools onboard mine` exports
  recent ones; write the expected facts yourself rather than copying the agent's answer.

## Commit messages

Short imperative subject (`Add Confluence child pages to read_wiki_page`), with a body
explaining why when it isn't obvious.
