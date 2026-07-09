"""let_go tests — pure filesystem logic on a temp vault, no network.

Run: uv run python tests/test_let_go.py
"""

import os
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from bower_bird import prune
from bower_bird.config import Config

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if not cond:
        _failures += 1
        print(f"FAIL: {msg}")


def _config(root: Path, ttl: int = 30) -> Config:
    return Config(
        telegram_bot_token="x",
        vault_path=root,
        model="test",
        build_model="test",
        state_path=root / "state.json",
        fetch_timeout=15,
        queue_limit=100,
        archive_ttl_days=30,
        inbox_ttl_days=ttl,
    )


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _write_inbox(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_let_go_moves_old_doc_and_ledgers() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        doc = cfg.inbox_dir / "Old Piece.md"
        _write_inbox(
            doc,
            f'---\ntitle: "Old Piece"\nsource: "https://example.com/a"\n'
            f"created: {_iso(40)}\n---\nbody\n",
        )

        moved = prune.let_go(cfg)

        check(not doc.exists(), "old doc removed from inbox/")
        check(
            (cfg.unread_archive_dir / "Old Piece.md").exists(),
            "old doc archived to unread/",
        )
        check(len(moved) == 1, "one log line returned")

        ledger = cfg.let_go_path.read_text(encoding="utf-8")
        check("Old Piece" in ledger, "ledger has title")
        check("https://example.com/a" in ledger, "ledger has url")
        check("let go after 40d unread" in ledger, "ledger has age")


def test_let_go_leaves_fresh_doc() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        doc = cfg.inbox_dir / "Fresh.md"
        _write_inbox(doc, f"---\ntitle: Fresh\ncreated: {_iso(5)}\n---\nbody\n")

        moved = prune.let_go(cfg)

        check(doc.exists(), "fresh doc left in inbox/")
        check(moved == [], "nothing logged")
        check(not cfg.let_go_path.exists(), "no ledger created")


def test_let_go_falls_back_to_mtime() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        doc = cfg.inbox_dir / "NoDate.md"
        _write_inbox(doc, "---\ntitle: No Date\n---\nbody\n")
        old = time.time() - 40 * 86400
        os.utime(doc, (old, old))

        prune.let_go(cfg)

        check(not doc.exists(), "mtime-aged doc (no created:) moved")
        check(
            (cfg.unread_archive_dir / "NoDate.md").exists(),
            "mtime-aged doc archived",
        )


def test_let_go_disabled_when_ttl_zero() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root, ttl=0)
        doc = cfg.inbox_dir / "Old.md"
        _write_inbox(doc, f"---\ncreated: {_iso(400)}\n---\nbody\n")

        moved = prune.let_go(cfg)

        check(moved == [], "ttl=0 returns no moves")
        check(doc.exists(), "ttl=0 leaves doc in place")


def test_let_go_skips_sync_conflict() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        doc = cfg.inbox_dir / "Piece.sync-conflict-20240101-someone.md"
        _write_inbox(doc, f"---\ncreated: {_iso(40)}\n---\nbody\n")

        moved = prune.let_go(cfg)

        check(doc.exists(), "sync-conflict copy left untouched")
        check(moved == [], "sync-conflict copy not logged")


def test_let_go_stamps_on_collision() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        cfg.unread_archive_dir.mkdir(parents=True, exist_ok=True)
        existing = cfg.unread_archive_dir / "Dup.md"
        existing.write_text("already archived", encoding="utf-8")

        doc = cfg.inbox_dir / "Dup.md"
        _write_inbox(doc, f"---\ntitle: Dup\ncreated: {_iso(40)}\n---\nbody\n")

        prune.let_go(cfg)

        check(existing.exists(), "pre-existing archived file survives")
        check(
            existing.read_text(encoding="utf-8") == "already archived",
            "pre-existing file untouched",
        )
        stamped = list(cfg.unread_archive_dir.glob("Dup.*.md"))
        check(len(stamped) == 1, "new doc archived under a collision stamp")


def main() -> int:
    test_let_go_moves_old_doc_and_ledgers()
    test_let_go_leaves_fresh_doc()
    test_let_go_falls_back_to_mtime()
    test_let_go_disabled_when_ttl_zero()
    test_let_go_skips_sync_conflict()
    test_let_go_stamps_on_collision()
    if _failures:
        print(f"\n{_failures} failure(s).")
        return 1
    print("OK: let_go tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
