# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-14

### Changed
- Renamed the package to `onboarding_agent` and the command to `onboarding-agent`, with flat
  subcommands (`onboarding-agent ask` instead of `devtools onboard ask`).

### Added
- Dockerfile for running the Slack app as a container, built and smoke-tested in CI.
- Eval set seeded with real questions and facts checked against the source repos.
- Pre-commit hooks for ruff, private-key detection and large files.

### Fixed
- Code search no longer fails when GitHub rejects a query it can't parse; it falls back to
  scanning the repo.

## [0.1.0] - 2026-11-14

### Added
- Slack agent answering from GitHub repos and Confluence, scoped to each user's own access.
- Commit-pinned code citations, Confluence page citations, and child-page listings.
- Code-search fallback that scans the default branch when GitHub's index has no results.
- Verified local-setup walkthroughs from `.onboarding/setup.yaml`.
- Weekly eval with leak, grounding, citation and fact checks; `doctor` command; CI.

[0.2.0]: https://github.com/atanus1502/dev-onboarding-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/atanus1502/dev-onboarding-agent/releases/tag/v0.1.0
