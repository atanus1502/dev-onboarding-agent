"""Command-line entry point: `onboarding-agent <command>`."""

import argparse
import logging
import os
import sys
from pathlib import Path

from onboarding_agent import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="onboarding-agent",
        description="Slack agent that answers new-hire questions from GitHub and Confluence.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="Check config and credentials (no model cost)")
    doctor.add_argument("--as", dest="as_user", help="Also show what this Slack user can read")
    sub.add_parser("serve", help="Run the Slack app (Socket Mode)")
    ask = sub.add_parser("ask", help="Ask one question as a Slack user, print the answer")
    ask.add_argument("--as", dest="as_user", required=True, help="Slack user id to act as")
    ask.add_argument("question")
    ev = sub.add_parser("eval", help="Run the eval set against the live agent")
    ev.add_argument("--cases", type=Path, default=Path("evals/questions.yaml"))
    ev.add_argument("--out", type=Path, default=Path("evals/results"))
    ev.add_argument("--baseline", type=Path, default=Path("evals/baseline.json"))
    ev.add_argument("--judge-model", default="claude-opus-5-5")
    mine = sub.add_parser("mine", help="Export recent real questions as eval candidates")
    mine.add_argument("--days", type=int, default=7)
    mine.add_argument("--out", type=Path, default=Path("evals/candidates.yaml"))

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env")  # real environment variables win over .env
    logging.basicConfig(level=logging.WARNING if args.command == "doctor" else logging.INFO)
    return _run(args)


def _run(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        from onboarding_agent.doctor import run_doctor

        return run_doctor(args.as_user)

    from onboarding_agent.config import Settings
    from onboarding_agent.runtime import build_agent

    settings = Settings.from_env()
    if args.command == "mine":
        from onboarding_agent.evals.mine import mine
        from onboarding_agent.store import Store

        print(
            f"wrote {mine(Store(settings.db_path), args.days, args.out)} candidates to {args.out}"
        )
        return 0

    if args.command == "serve":
        from onboarding_agent.runtime import agent_from, build_services, commands_from
        from onboarding_agent.slack_app import serve

        services = build_services(settings)
        serve(agent_from(services), services.store, commands_from(services))
        return 0

    agent, store = build_agent(settings)
    if args.command == "ask":
        answer = agent.answer(args.as_user, args.question)
        print(answer.text)
        print("\nsources:", *[f"  {s.url}" for s in answer.sources], sep="\n")
        return 0

    from onboarding_agent.evals.runner import load_cases, run

    _, problems, report = run(
        agent, load_cases(args.cases), args.out, args.judge_model, args.baseline
    )
    print(report)
    if channel := os.environ.get("SLACK_EVAL_CHANNEL"):
        from slack_sdk import WebClient

        WebClient(token=os.environ["SLACK_BOT_TOKEN"]).chat_postMessage(
            channel=channel, text=report
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
