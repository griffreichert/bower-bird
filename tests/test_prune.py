"""Prune tests — pure filesystem logic on a temp vault, no network.

Run: uv run python tests/test_prune.py
"""

import os
import tempfile
import time
from pathlib import Path

from bower_bird import prune
from bower_bird.config import Config
from bower_bird.state import State

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if not cond:
        _failures += 1
        print(f"FAIL: {msg}")


def _config(root: Path) -> Config:
    return Config(
        telegram_bot_token="x",
        vault_path=root,
        model="test",
        build_model="test",
        state_path=root / "state.json",
        fetch_timeout=15,
        queue_limit=100,
        archive_ttl_days=30,
    )


def _write_old(path: Path, text: str, age_days: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))


def test_prune_deletes_only_captured_and_old() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        state = State.load(cfg.state_path)

        # captured husk (matching source note), old → deleted
        _write_old(cfg.archive_dir / "Captured.md", "source: https://a.co\nbody", 60)
        (cfg.sources_dir).mkdir(parents=True, exist_ok=True)
        (cfg.sources_dir / "Captured.md").write_text("# node", encoding="utf-8")

        # captured via URL in state, archived under a collision stamp → deleted
        _write_old(
            cfg.archive_dir / "ReClip.20250101-120000.md",
            'source: "https://b.co/post"\nbody',
            60,
        )
        state.mark_url("https://b.co/post")

        # orphan (no node, url not in state), old → HELD BACK
        _write_old(cfg.archive_dir / "Orphan.md", "source: https://c.co\nbody", 60)

        # captured but too young → skipped
        _write_old(cfg.archive_dir / "Fresh.md", "source: https://d.co\nbody", 5)
        (cfg.sources_dir / "Fresh.md").write_text("# node", encoding="utf-8")

        prune.prune(cfg, state, prompt=lambda _: "y")

        check(not (cfg.archive_dir / "Captured.md").exists(), "old+captured deleted")
        check(
            not (cfg.archive_dir / "ReClip.20250101-120000.md").exists(),
            "stamped husk matched by url deleted",
        )
        check((cfg.archive_dir / "Orphan.md").exists(), "orphan held back, not deleted")
        check((cfg.archive_dir / "Fresh.md").exists(), "young husk skipped")


def test_prune_aborts_on_no() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        state = State.load(cfg.state_path)
        _write_old(cfg.archive_dir / "X.md", "source: https://a.co\nb", 60)
        (cfg.sources_dir).mkdir(parents=True, exist_ok=True)
        (cfg.sources_dir / "X.md").write_text("# node", encoding="utf-8")

        prune.prune(cfg, state, prompt=lambda _: "n")
        check((cfg.archive_dir / "X.md").exists(), "default-No leaves files intact")


def test_prune_no_archive() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        # no archive/ dir → no crash, returns 0
        rc = prune.prune(cfg, State.load(cfg.state_path), prompt=lambda _: "y")
        check(rc == 0, "missing archive/ handled cleanly")


def main() -> int:
    test_prune_deletes_only_captured_and_old()
    test_prune_aborts_on_no()
    test_prune_no_archive()
    if _failures:
        print(f"\n{_failures} failure(s).")
        return 1
    print("OK: prune tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
