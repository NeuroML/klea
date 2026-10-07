#!/usr/bin/env python3
"""
Shared async REPL for Klea chat interfaces.

File: klea_utils/ui/tui/repl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""


def _prompt_interrupt(ask: dict, app_prefix: str) -> tuple[dict | None, bool]:
    """Prompt for a HITL ask and return ``(interrupt_response, cancel)``.

    A ``review`` ask offers approve / request changes / cancel; an ``input``
    ask prompts one answer per question.  ``(None, True)`` means cancel.

    :param ask: The interrupt ``data`` (``kind``, ``question``/``questions``).
    :param app_prefix: Prefix for the input prompts.
    :returns: The resume mapping to send, and whether the user cancelled.
    """
    kind = ask.get("kind", "input")
    print("*** The agent needs your input ***")
    if kind == "review":
        while True:
            choice = (
                input(
                    f"{app_prefix} (REVIEW) [a]pprove / [r]equest changes / "
                    "[c]ancel >>> "
                )
                .strip()
                .lower()
            )
            if choice in ("a", "approve"):
                return {"decision": "approve"}, False
            if choice in ("r", "revise", "request changes"):
                feedback = input(f"{app_prefix} (REVIEW) feedback >>> ").strip()
                return {"decision": "revise", "feedback": feedback}, False
            if choice in ("c", "cancel"):
                return None, True
            print("Please choose a, r, or c.")

    questions = ask.get("questions")
    if not isinstance(questions, list) or not questions:
        questions = [{"question": ask.get("question") or "More information is needed."}]
    answers: list[str] = []
    for question in questions:
        answer = input(
            f"{app_prefix} (INPUT) {question.get('question', '')} (or 'cancel') >>> "
        ).strip()
        if answer.lower() == "cancel":
            return None, True
        answers.append(answer)
    return {"answers": answers}, False


async def run_repl(
    url: str,
    title: str,
    single_query: str = "",
    app_prefix: str = "klea",
    app_name: str = "klea-tui",
) -> None:
    """Run an interactive or single-query chat REPL.

    Connects to the API server's SSE ``/query/stream`` endpoint and
    displays progress labels and the final answer.

    In interactive mode each query is sent as the user types it;
    the REPL does not batch queries.

    :param url: Base URL of the API server (e.g. ``http://127.0.0.1:8005``)
    :param title: Application title displayed on start
    :param single_query: If set, run one query and exit instead of REPL loop
    :param app_prefix: Prefix for user/assistant labels (e.g. ``"klea"``)
    :param app_name: Log identity for this frontend, used as the log file
        name so each process keeps its own logs (e.g. ``"klea-rag-tui"``)
    """
    # Configure process-wide logging for this client process.  Lazy:
    # platformdirs / plogging imports are cheap, and everything else is
    # deferred below so --help stays fast.
    from platformdirs import PlatformDirs

    from klea_utils.plogging import resolve_log_level, setup_root_logger

    setup_root_logger(
        app_name,
        stderr_level=resolve_log_level(),
        log_dir=PlatformDirs(app_name).user_data_dir,
    )

    # Lazy: avoids importing yaspin (and its deps) at module level; httpx is
    # only needed to catch a dropped/timed-out or rejected stream below.
    import coolname
    import httpx
    from yaspin import yaspin

    from klea_utils.api.sse import stream_events
    from klea_utils.api.utils import check_api_is_ready

    chat_id = coolname.generate_slug(2)

    with yaspin(text="Waiting for API..."):
        await check_api_is_ready(f"{url}/health/ready")

    async def _query_one(query: str) -> None:
        """Stream one turn, answering any HITL interrupt it pauses on."""
        resume = False
        interrupt_response: dict | None = None
        interrupt_id: str | None = None
        interrupt_cancel = False
        while True:
            full_response = ""
            error_msg = ""
            ask: dict | None = None
            print()

            with yaspin(text="Working ...", timer=True) as spinner:
                try:
                    async for event in stream_events(
                        query,
                        chat_id,
                        url,
                        resume=resume,
                        interrupt_response=interrupt_response,
                        interrupt_id=interrupt_id,
                        interrupt_cancel=interrupt_cancel,
                    ):
                        etype = event["type"]
                        if etype == "ping":
                            # Server heartbeat: nothing to display.
                            continue
                        if etype == "progress":
                            spinner.text = (event.get("data") or {}).get(
                                "heading"
                            ) or event["node"]
                        elif etype == "interrupt":
                            ask = event.get("data") or {}
                            break
                        elif etype == "complete":
                            full_response = event.get("message_for_user", "")
                            spinner.ok("[OK]")
                        elif etype == "error":
                            error_msg = event.get("message", "Unknown server error")
                            spinner.fail("[ERROR]")
                            break
                except httpx.HTTPError as e:
                    # A dropped/timed-out stream or a rejected request (e.g.
                    # HTTP 409): report it as the turn's error instead of
                    # letting the exception crash the REPL.
                    error_msg = f"Request failed: {e}"
                    spinner.fail("[ERROR]")

            if ask is not None:
                # Paused for human input: prompt, then stream the answer (which
                # may itself pause again) in the same turn.
                interrupt_response, interrupt_cancel = _prompt_interrupt(
                    ask, app_prefix
                )
                interrupt_id = ask.get("interrupt_id")
                query = ""
                resume = False
                continue

            output = error_msg or full_response
            label = "(ERROR)" if error_msg else "(AI)"
            print(f"{app_prefix} {label} >>> {output}")
            print("\n" + "-" * 40 + "\n")
            return

    if single_query:
        print(f"{app_prefix} (USER) >>> {single_query}")
        await _query_one(single_query)
        return

    print(f"*** {title} ({url}) ***")
    print("Please note that answers are generated by LLMs and may be incorrect.")
    print()
    print("Type 'quit' to exit.")
    print("\n" + "-" * 40 + "\n")

    while True:
        q = input(f"{app_prefix} (USER) >>> ")
        if q.lower() == "quit":
            break
        await _query_one(q)

    print(f"\n{app_prefix} >>> Bye!")
