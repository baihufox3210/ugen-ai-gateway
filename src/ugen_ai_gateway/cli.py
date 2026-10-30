"""Administration from the server's command line.

    python -m ugen_ai_gateway.cli list-users
    python -m ugen_ai_gateway.cli reset-password <username> [--password <new>]
    python -m ugen_ai_gateway.cli make-admin <username>
    python -m ugen_ai_gateway.cli check-routing [--limit N]
    python -m ugen_ai_gateway.cli diagnose-analysis "first message" ["second message" ...]

With Docker: docker compose exec gateway python -m ugen_ai_gateway.cli ...
"""

import argparse
import asyncio
import json
import secrets
import sys
import time

from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.services.usage import UsageStore


def _store() -> UsageStore:
    path = get_settings().usage_db_path
    if not path:
        sys.exit("USAGE_DB_PATH is empty, so there are no accounts.")
    return UsageStore(path)


def _user(store: UsageStore, username: str) -> dict:
    user = store.get_user_sync(username)
    if user is None:
        sys.exit(f"No account named {username!r}.")
    return user


def _audit(store: UsageStore, event: str, user_id: str) -> None:
    store._append_audit(event, None, {"actor": "command_line", "user_id": user_id})


async def _check_routing(limit: int | None) -> None:
    """Run the labeled requests through this gateway's analysis, as configured
    (rules plus the analysis model), and report how often the local-or-cloud
    call matches the label."""
    from ugen_ai_gateway.analysis.complexity import CASES_FILE
    from ugen_ai_gateway.api.schemas import ChatRequest
    from ugen_ai_gateway.dependencies import get_gateway_service

    service = get_gateway_service()
    cases = json.loads(CASES_FILE.read_text("utf-8"))[:limit]
    sources: dict[str, int] = {}
    wrong: list[tuple[str, str, str, str]] = []
    gate = asyncio.Semaphore(4)

    async def check(case: dict) -> None:
        messages = []
        if case["previous"]:
            messages += [
                {"role": "user", "content": case["previous"]},
                {"role": "assistant", "content": "…"},
            ]
        messages.append({"role": "user", "content": case["text"]})
        request = ChatRequest(messages=messages)
        async with gate:
            analysis, _, source = await service._analyze(
                request,
                "\n".join(m["content"] for m in messages if m["role"] == "user"),
            )
        sources[source] = sources.get(source, 0) + 1
        if (analysis.complexity == "simple") != (case["label"] == "simple"):
            wrong.append((source, analysis.complexity, case["label"], case["text"]))
        done = sum(sources.values())
        if done % 25 == 0:
            print(f"  {done}/{len(cases)}", file=sys.stderr)

    await asyncio.gather(*(check(case) for case in cases))
    total = len(cases)
    print(f"{total - len(wrong)}/{total} right ({(total - len(wrong)) / total:.1%})")
    print("decided by: " + ", ".join(f"{k} {v}" for k, v in sorted(sources.items())))
    for source, got, label, text in wrong:
        print(f"  [{source}] got {got}, expected {label}: {text[:100]!r}")


def _show(text: str, limit: int = 3000) -> str:
    text = (
        text
        if len(text) <= limit
        else text[:limit] + f"… [{len(text) - limit} more characters]"
    )
    return "\n".join("    | " + line for line in text.splitlines() or [""])


async def _diagnose_analysis(
    turns: list[str], *, with_system_prompt: bool, always_ask: bool
) -> None:
    """Run the analysis step the way a real conversation would, and print every
    call to the analysis model with exactly what it answered."""
    from ugen_ai_gateway.api.prompt_routes import system_prompt
    from ugen_ai_gateway.api.schemas import ChatRequest
    from ugen_ai_gateway.dependencies import get_gateway_service, get_usage_store

    settings = get_settings()
    service = get_gateway_service()
    if always_ask:
        service._settings = service._settings.model_copy(
            update={"analysis_mode": "llm"}
        )
    analyzer = service._analyzer
    provider = analyzer._provider
    print("== Analysis setup")
    print(f"   mode                 {service._settings.analysis_mode}")
    print(
        f"   analysis model       {analyzer._model} (provider {getattr(provider, 'name', '?')})"
    )
    print(f"   thinking setting     {analyzer._reasoning.model_dump()}")
    print(
        f"   max reply tokens     {analyzer._max_tokens} (ANALYSIS_MAX_TOKENS={settings.analysis_max_tokens})"
    )
    print(f"   hide thinking        {service.routing.hide_thinking}")

    calls: list[int] = []
    original = provider.complete

    async def traced(**kwargs):
        calls.append(1)
        number = len(calls)
        # The calls run side by side; each prints as one block when it ends.
        out: list[str] = []
        options = {k: v for k, v in kwargs.items() if k != "messages"}
        text = kwargs["messages"][-1]["content"]
        out.append(f"\n   -- call {number} to the analysis model")
        out.append(f"      options: {options}")
        out.append(f"      text sent ({len(text)} characters):")
        out.append(_show(text, 400))
        started = time.perf_counter()
        try:
            completion = await original(**kwargs)
        except Exception as exc:
            out.append(
                f"      THE REQUEST FAILED after {time.perf_counter() - started:.1f}s: "
                f"{type(exc).__name__}: {exc}"
            )
            print("\n".join(out))
            raise
        choice = completion.choices[0]
        message = choice.message
        usage = getattr(completion, "usage", None)
        out.append(
            f"      answered in {time.perf_counter() - started:.1f}s · "
            f"finish_reason={choice.finish_reason} · "
            f"completion_tokens={getattr(usage, 'completion_tokens', '?')}"
        )
        for field in ("reasoning_content", "reasoning"):
            extra = getattr(message, field, None)
            if extra:
                out.append(f"      {field} ({len(extra)} characters):")
                out.append(_show(extra, 1500))
        content = message.content
        out.append(
            f"      content ({len(content or '')} characters), exactly as received:"
        )
        out.append(_show(content or "", 3000))
        if choice.finish_reason == "length":
            out.append(
                "      >>> The reply was cut off by the token limit before it finished."
            )
        print("\n".join(out))
        return completion

    provider.complete = traced
    store = get_usage_store()
    system = system_prompt(store) if with_system_prompt else ""
    messages: list[dict] = [{"role": "system", "content": system}] if system else []
    trusted = 1 if system else 0
    failures = 0
    for number, turn in enumerate(turns, start=1):
        messages.append({"role": "user", "content": turn})
        print(f"\n== Message {number}: {turn!r}")
        calls.clear()
        service._analysis_cache = type(service._analysis_cache)(
            max_size=0, ttl_seconds=1
        )
        request = ChatRequest(messages=messages, trusted_messages=trusted)  # type: ignore[arg-type]
        try:
            analysis, millis, source = await service._analyze(request, turn)
            print(
                f"\n   RESULT ({millis:.0f} ms, {len(calls)} call(s), by {source}): difficulty={analysis.complexity} "
                f"task={analysis.task_type} personal_data={analysis.personal_data_types or 'none'}"
            )
        except Exception as exc:  # noqa: BLE001 - a diagnostic reports anything
            failures += 1
            print(f"\n   FAILED: {type(exc).__name__}: {exc}")
            if exc.__cause__ is not None:
                print(f"   because: {type(exc.__cause__).__name__}: {exc.__cause__}")
        messages.append({"role": "assistant", "content": "(an earlier answer)"})
    print("\n== Summary")
    if not failures:
        print("   Every message was analyzed.")
    else:
        print(
            f"   {failures} of {len(turns)} message(s) failed. Read the 'content' above:"
        )
        print(
            "   - It starts with thinking text, or is cut off: turn thinking off for this model"
        )
        print("     (Models page -> Thinking) or raise ANALYSIS_MAX_TOKENS.")
        print(
            "   - It is empty: the model used its whole reply on thinking (finish_reason=length)."
        )
        print(
            "   - The request itself failed: the server rejected a parameter shown in 'options'."
        )
    if not calls:
        print(
            "   (The rules alone decided, so the analysis model was not called. Use --always-ask "
            "or set the analysis mode to Thorough to force it.)"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ugen_ai_gateway.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-users", help="list accounts")
    reset = commands.add_parser("reset-password", help="set a new password")
    reset.add_argument("username")
    reset.add_argument("--password", help="the new password (random when omitted)")
    admin = commands.add_parser("make-admin", help="make an account an administrator")
    admin.add_argument("username")
    check = commands.add_parser(
        "check-routing",
        help="measure how well requests are judged local or cloud, with this "
        "gateway's analysis model",
    )
    check.add_argument("--limit", type=int, help="only the first N requests")
    diagnose = commands.add_parser(
        "diagnose-analysis",
        help="run the difficulty check on a conversation and print each call to "
        "the analysis model with its raw answer",
    )
    diagnose.add_argument("messages", nargs="+", help="the user's messages, in order")
    diagnose.add_argument(
        "--no-system-prompt",
        action="store_true",
        help="leave out the administrators' system prompt",
    )
    diagnose.add_argument(
        "--always-ask",
        action="store_true",
        help="call the analysis model even when the rules could decide",
    )
    args = parser.parse_args(argv)

    if args.command == "diagnose-analysis":
        asyncio.run(
            _diagnose_analysis(
                args.messages,
                with_system_prompt=not args.no_system_prompt,
                always_ask=args.always_ask,
            )
        )
        return

    if args.command == "check-routing":
        asyncio.run(_check_routing(args.limit))
        return

    store = _store()
    if args.command == "list-users":
        for user in store._list_users():
            role = "admin" if user["is_admin"] else "member"
            state = " (disabled)" if user["disabled"] else ""
            created = time.strftime("%Y-%m-%d", time.localtime(user["created_at"]))
            print(f"{user['username']:<32} {role:<7} created {created}{state}")
    elif args.command == "reset-password":
        user = _user(store, args.username)
        password = args.password or secrets.token_urlsafe(12)
        if len(password) < 8:
            sys.exit("Use a password of at least 8 characters.")
        store.set_password_sync(user["id"], password)
        store._update_user(user["id"], {"disabled": False})
        _audit(store, "password_reset", user["id"])
        print(f"New password for {user['username']}: {password}")
        print("Every session of this account was signed out.")
    elif args.command == "make-admin":
        user = _user(store, args.username)
        store._update_user(user["id"], {"is_admin": True, "disabled": False})
        _audit(store, "account_updated", user["id"])
        print(f"{user['username']} is now an administrator.")


if __name__ == "__main__":
    main()
