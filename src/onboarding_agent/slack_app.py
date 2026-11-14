"""Slack front end (Bolt, Socket Mode). Answers DMs and @mentions in threads."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from anthropic.types.beta import BetaMessageParam
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from onboarding_agent.agent import Answer, OnboardingAgent
from onboarding_agent.store import Store

log = logging.getLogger(__name__)

DM_HISTORY_LIMIT = 20
FEEDBACK = {"+1": 1, "thumbsup": 1, "-1": -1, "thumbsdown": -1}
PRIVATE_NOTE = (
    "I sent you my answer privately: it draws on repos or pages that not everyone here can see."
)


def _strip_mention(text: str, bot_user_id: str | None) -> str:
    if bot_user_id:
        text = text.replace(f"<@{bot_user_id}>", "")
    return re.sub(r"\s+", " ", text).strip() if "```" not in text else text.strip()


def thread_history(
    messages: list[dict[str, Any]], bot_user_id: str | None, current_ts: str
) -> list[BetaMessageParam]:
    """Earlier thread messages as alternating-ish user/assistant turns (the API merges
    consecutive same-role turns). Everything here is already visible to the asker."""
    history: list[BetaMessageParam] = []
    for m in messages:
        if m.get("ts") == current_ts or not m.get("text"):
            continue
        if m.get("user") == bot_user_id or m.get("bot_id"):
            history.append({"role": "assistant", "content": m["text"]})
        else:
            text = _strip_mention(m["text"], bot_user_id)
            history.append({"role": "user", "content": f"<@{m.get('user')}>: {text}"})
    while history and history[0]["role"] == "assistant":
        history.pop(0)  # the API requires the first turn to be the user's
    return history


def post_answer(
    client: WebClient,
    channel: str,
    channel_type: str,
    user: str,
    thread_ts: str | None,
    answer: Answer,
) -> str | None:
    """Post publicly unless the answer used restricted sources in a shared channel."""
    if channel_type == "im" or not answer.restricted:
        # Every answer cites several permalinks; unfurling them buries the answer under cards.
        resp = client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=answer.text,
            unfurl_links=False,
            unfurl_media=False,
        )
        return str(resp["ts"])
    client.chat_postEphemeral(channel=channel, user=user, thread_ts=thread_ts, text=answer.text)
    client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PRIVATE_NOTE)
    return None


def create_app(agent: OnboardingAgent, store: Store) -> App:
    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    def handle(event: dict[str, Any], client: WebClient, bot_user_id: str | None) -> None:
        channel, user, ts = event["channel"], event["user"], event["ts"]
        channel_type = event.get("channel_type", "channel")
        # DMs read like a chat, so answer inline unless the user is already in a thread;
        # in channels always thread, to keep the channel readable.
        thread_ts = event.get("thread_ts") or (None if channel_type == "im" else ts)
        question = _strip_mention(event.get("text", ""), bot_user_id)

        history: list[BetaMessageParam] = []
        if thread_ts and event.get("thread_ts"):
            replies = client.conversations_replies(channel=channel, ts=thread_ts, limit=50)
            history = thread_history(replies.get("messages", []), bot_user_id, ts)
        elif channel_type == "im":
            # Inline DMs carry follow-ups (e.g. a setup walkthrough) in the recent history.
            recent = client.conversations_history(channel=channel, limit=DM_HISTORY_LIMIT)
            messages: list[dict[str, Any]] = list(
                reversed(recent.get("messages", []))
            )  # API returns newest first
            history = thread_history(messages, bot_user_id, ts)

        client.reactions_add(channel=channel, timestamp=ts, name="eyes")
        try:
            answer = agent.answer(user, question, history)
        except Exception:
            log.exception("agent failed")
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="Something went wrong on my side. Try again, or ask in #eng-help.",
            )
            return
        finally:
            client.reactions_remove(channel=channel, timestamp=ts, name="eyes")

        posted_ts = post_answer(client, channel, channel_type, user, thread_ts, answer)
        store.log_interaction(
            user, channel, posted_ts, question, answer.text, [s.url for s in answer.sources]
        )

    @app.event("app_mention")
    def on_mention(event: dict[str, Any], client: WebClient, context: dict[str, Any]) -> None:
        handle(event, client, context.get("bot_user_id"))

    @app.event("message")
    def on_dm(event: dict[str, Any], client: WebClient, context: dict[str, Any]) -> None:
        if event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id"):
            return
        handle(event, client, context.get("bot_user_id"))

    @app.event("reaction_added")
    def on_reaction(event: dict[str, Any]) -> None:
        score = FEEDBACK.get(event.get("reaction", ""))
        item = event.get("item", {})
        if score is not None and item.get("type") == "message":
            store.record_feedback(item["channel"], item["ts"], score)

    return app


def serve(agent: OnboardingAgent, store: Store) -> None:
    SocketModeHandler(create_app(agent, store), os.environ["SLACK_APP_TOKEN"]).start()  # type: ignore[no-untyped-call]
