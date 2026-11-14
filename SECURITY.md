# Security policy

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's
[private vulnerability reporting](../../security/advisories/new) for this repository, and
include steps to reproduce. You'll get a reply within a few days.

Permission bypasses are the most important class of bug here: any way to make the agent read,
quote or link a repo or Confluence page that the asking user can't access. Reports of prompt
injection that lead to that are in scope.

## How the agent limits what it can expose

- Every tool call checks the asking user's own GitHub and Confluence access before touching a
  source, and checks fail closed (unlinked users, API errors and timeouts all deny).
- A missing resource and a forbidden one return the same message, so the agent never confirms
  that something exists.
- In shared channels, answers built from sources not marked `org_wide` are sent only to the
  asker.
- Setup steps are graded against the user's pasted output, never against model-written text.

Details: [docs/onboarding-agent.md](docs/onboarding-agent.md#permission-model).

## Handling credentials

- Secrets live in `.env` (gitignored). Never commit tokens, and never paste them into issues.
- Use least privilege: a GitHub App with Metadata and Contents read only, a Confluence service
  account limited to the spaces you configure, and the Slack scopes in
  `config/slack-manifest.yaml`.
- `config/identities.yaml` maps people to accounts and is gitignored; commit only the example.
- If a token is exposed, revoke it at the provider first, then replace it in `.env`.
