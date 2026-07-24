"""Drift guard: the canonical vault CLAUDE.md must name every owned path.

The vault docs (vault/CLAUDE.md, synced into BowerBird/ by `make sync-vault`)
describe the folder model. If someone adds an owned path to config.py but forgets
to document it, Claude Code's Tier-2 playbook goes stale — exactly the bug that
hid `trinkets/` and `tools.md`. This test reads every `vault_path / "<name>"`
literal out of config.py and asserts each appears in the doc.

Run: .venv/bin/python tests/test_vault_docs.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "src" / "bower_bird" / "config.py"
DOC = ROOT / "vault" / "CLAUDE.md"

# Owned paths intentionally NOT surfaced in the Tier-2 playbook (Claude Code
# never reads/writes them directly — they're Tier-1/CLI plumbing or its own
# output). Keep this list tiny and justified.
EXEMPT = {
    "digests",  # Claude creates this itself; documented by behavior, not layout
}


def owned_paths() -> set[str]:
    src = CONFIG.read_text(encoding="utf-8")
    return set(re.findall(r'vault_path / "([^"]+)"', src))


def main() -> int:
    doc = DOC.read_text(encoding="utf-8")
    missing = [
        name for name in sorted(owned_paths()) if name not in EXEMPT and name not in doc
    ]
    if missing:
        print(f"FAIL: vault/CLAUDE.md does not mention owned path(s): {missing}")
        print("  → document them in vault/CLAUDE.md (or add to EXEMPT with reason).")
        return 1
    print("OK: vault/CLAUDE.md names every owned path in config.py.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    raise SystemExit(main())
