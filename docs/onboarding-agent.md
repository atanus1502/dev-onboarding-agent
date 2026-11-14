# Onboarding agent

New hires DM the bot or @mention it in a channel. It answers from the configured GitHub repos
and Confluence spaces, links every claim to a permalink or page, and walks people through local
setup step by step, checking each step against the output they paste.

## How a question flows

```
Slack event ─► slack_app.handle ─► OnboardingAgent.answer(slack_user, question, thread)
                                      │ IdentityResolver: Slack id → GitHub login + Atlassian id
                                      │ build_tools(AskContext)  ← closures bound to that identity
                                      ▼
                          Claude tool runner (claude-opus-5-5)
                 list_repositories · search_code · read_file · list_directory
                 search_wiki · read_wiki_page · get_setup_guide · check_setup_step
                                      │ every call → PermissionChecker (live, 5 min cache)
                                      ▼
                    Answer(text, sources) ─► public reply, or ephemeral when restricted
```

## Permission model

The bot reads with its own credentials (a GitHub App installation token and a Confluence
service account), so access control is enforced in code on every tool call rather than left to
the prompt:

- **Repos:** `GET /repos/{repo}/collaborators/{login}/permission`, which includes team and
  org-base access. Anything other than `none` counts as readable. Search is limited with
  `repo:` qualifiers to repos the user can read, so hits from other repos never reach the model.
- **Wiki:** `POST /wiki/rest/api/content/{id}/permission/check` per page, which respects page
  restrictions inside spaces the user can otherwise read. Search results are filtered page by page.
- **Fail closed:** unlinked users, API errors and timeouts all deny. Failures aren't cached;
  results are cached for `permission_cache_ttl` (default 300 s), so a revocation takes effect
  within five minutes.
- **No existence oracle:** a missing resource and a forbidden one return the same message.
- **Channel visibility:** if an answer drew on any source not marked `org_wide` in
  `config/sources.yaml`, it's sent ephemerally to the asker and the thread gets a short note
  instead, because others in the channel may lack that access. DMs always get the full reply.

Identity mapping is a static YAML file (`config/identities.yaml`). Replacing it with an OAuth
"link your accounts" flow, or with your IdP's SCIM data, only needs a new `IdentityResolver`.

## Setup walkthroughs

Each repo can ship `.onboarding/setup.yaml` (format in `setup_guides.py`). The agent hands out
one step at a time, asks the user to run that step's verify command and paste the output, then
calls `check_setup_step`. That tool matches the step's regex against the user's actual Slack
message, not against text the model passes in, so the model can't mark a step passed on its
own. Progress is stored per user in SQLite, so a walkthrough can resume days later.

## Evals

`evals/questions.yaml` holds real new-hire questions, each asked as a real Slack user, with
engineer-written expected facts. `onboarding-agent eval` grades every case on:

| Check | How | Fails CI |
| --- | --- | --- |
| Leak | agent read a source matching `must_not_access` | always |
| Grounded links | every link in the answer was returned by a tool | per case |
| Citations | each `must_cite` substring appears in a linked URL | per case |
| Facts | Claude judge (structured output): every fact present, none contradicted | per case |

The run fails on any leak, or if the pass rate drops more than 5 points below
`evals/baseline.json` (copy a good run's `summary` there). `.github/workflows/weekly-evals.yml`
runs it every Monday and posts the summary to `SLACK_EVAL_CHANNEL`.

To grow the set: `onboarding-agent mine --days 7` exports the week's real questions, with
thumbs-down ones first, to `evals/candidates.yaml`. An engineer fills in `facts` and citations
and moves the good ones into `questions.yaml`. Don't copy the agent's answer in as the expected
facts. Include cases asked by people *without* access, with `must_not_access` set.

Each case costs a full agent run plus one judge call. Measure a run's cost on the real set
before scheduling it at scale.

## Deploying

1. Create the Slack app from `config/slack-manifest.yaml`; generate an app-level token with
   `connections:write` (`SLACK_APP_TOKEN`) and install it to get `SLACK_BOT_TOKEN`.
2. Create a GitHub App with **Metadata: read** and **Contents: read**, install it on the
   configured repos, and set `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH` and
   `GITHUB_APP_INSTALLATION_ID`. Installation tokens are minted and refreshed automatically
   (`sources/github_auth.py`). A fine-grained PAT in `GITHUB_TOKEN` also works for trying it out.
3. Create a Confluence service account that can read the configured spaces, plus an API token.
4. Copy `config/*.example.yaml` and `.env.example`, fill them in (`.env` is loaded automatically).
5. `uv run onboarding-agent doctor --as <slack id>` checks every credential and source with free
   read-only calls and lists what that user can read. Fix anything marked FAIL, then
   `uv run onboarding-agent serve`.
6. To run it permanently, use the container instead: see "Run it as a container" in the
   [README](../README.md#run-it-as-a-container).

Try it without Slack: `uv run onboarding-agent ask --as U0123ABCD "how do I run payments-api?"`
