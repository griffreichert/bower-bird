"""Telegram pull-loop tests — OSError leaves the offset for a retry, other
errors ack and move on. No network: telegram + _handle are stubbed.

Run: uv run python tests/test_pull.py
"""

import tempfile
from pathlib import Path

from bower_bird import app
from bower_bird.config import Config
from bower_bird.state import State
from bower_bird.telegram import Update

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if not cond:
        _failures += 1
        print(f"FAIL: {msg}")


def _config(root: Path) -> Config:
    return Config(
        telegram_bot_token="x",
        # Pin the allowlist: the real .env leaks in otherwise and the fake
        # chat_id 1 gets dropped as an unauthorized sender.
        allowed_chat_ids="1",
        vault_path=root,
        model="test",
        build_model="test",
        state_path=root / "state.json",
        fetch_timeout=15,
        queue_limit=100,
    )


def _run_pull(handle, updates: list[Update]) -> tuple[State, int, list[str]]:
    sent: list[str] = []
    orig = (app.telegram.get_updates, app.telegram.send_message, app._handle)
    app.telegram.get_updates = lambda *a, **k: updates
    app.telegram.send_message = lambda token, chat_id, text, **k: sent.append(text)
    app._handle = handle
    try:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "BowerBird"
            root.mkdir(parents=True)
            state = State(path=root / "state.json")
            processed = app._pull_telegram(_config(root), state)
            return state, processed, sent
    finally:
        app.telegram.get_updates, app.telegram.send_message, app._handle = orig


def test_oserror_leaves_offset() -> None:
    def handle(config, state, text):
        raise OSError(11, "Resource deadlock avoided")

    updates = [Update(update_id=7, chat_id=1, text="https://a.example")]
    state, processed, sent = _run_pull(handle, updates)
    check(state.telegram_offset == 0, "OSError must not ack the update")
    check(processed == 0, "OSError update must not count as processed")
    check(sent == [], "no receipt for a retried update")


def test_other_error_acks() -> None:
    def handle(config, state, text):
        raise ValueError("poison")

    updates = [Update(update_id=7, chat_id=1, text="junk")]
    state, processed, sent = _run_pull(handle, updates)
    check(state.telegram_offset == 8, "poison message must ack so it can't loop")
    check(sent == ["⚠️ Couldn't process that one."], "generic receipt, no detail")


def test_success_acks_and_receipts() -> None:
    updates = [Update(update_id=7, chat_id=1, text="ok")]
    state, processed, sent = _run_pull(lambda c, s, t: "done", updates)
    check(state.telegram_offset == 8, "success advances the offset")
    check(processed == 1, "success counts as processed")
    check(sent == ["done"], "receipt is the handler's return")


def main() -> int:
    test_oserror_leaves_offset()
    test_other_error_acks()
    test_success_acks_and_receipts()
    if _failures:
        print(f"\n{_failures} failure(s).")
        return 1
    print("OK: pull-loop tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
