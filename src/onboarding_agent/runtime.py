"""Wires the agent's dependencies from Settings."""

from __future__ import annotations

import anthropic

from onboarding_agent.agent import OnboardingAgent
from onboarding_agent.config import Settings
from onboarding_agent.identity import StaticIdentityResolver
from onboarding_agent.permissions import LivePermissionChecker
from onboarding_agent.sources.confluence import ConfluenceClient
from onboarding_agent.sources.github import GitHubClient
from onboarding_agent.sources.github_auth import AppInstallationToken, TokenProvider
from onboarding_agent.store import Store


def github_credentials(settings: Settings) -> str | TokenProvider:
    if settings.github_app_id:
        if not (settings.github_app_private_key_path and settings.github_app_installation_id):
            raise ValueError(
                "GITHUB_APP_ID needs GITHUB_APP_PRIVATE_KEY_PATH and GITHUB_APP_INSTALLATION_ID"
            )
        return AppInstallationToken(
            settings.github_app_id,
            settings.github_app_private_key_path.read_text(),
            settings.github_app_installation_id,
            settings.github_api_url,
        )
    if settings.github_token:
        return settings.github_token
    raise ValueError("set GITHUB_TOKEN, or GITHUB_APP_ID with its private key and installation id")


def build_github(settings: Settings) -> GitHubClient:
    return GitHubClient(
        github_credentials(settings), settings.github_api_url, settings.github_web_url
    )


def build_confluence(settings: Settings) -> ConfluenceClient | None:
    url, email, token = (
        settings.confluence_base_url,
        settings.confluence_email,
        settings.confluence_api_token,
    )
    if not url:
        return None
    if not (email and token):
        raise ValueError("CONFLUENCE_BASE_URL needs CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN")
    return ConfluenceClient(url, email, token)


def build_agent(settings: Settings, store: Store | None = None) -> tuple[OnboardingAgent, Store]:
    github = build_github(settings)
    confluence = build_confluence(settings)
    store = store or Store(settings.db_path)
    agent = OnboardingAgent(
        client=anthropic.Anthropic(),
        settings=settings,
        identities=StaticIdentityResolver.from_file(settings.identities_path),
        permissions=LivePermissionChecker(github, confluence, ttl=settings.permission_cache_ttl),
        github=github,
        confluence=confluence,
        store=store,
    )
    return agent, store
