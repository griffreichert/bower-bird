"""6-doc pilot: run sample sends through the NEW ingest into a scratch folder.

Eyeball the antilibrary pipeline (#25) on real content BEFORE any real-vault
write: the 4 tweet archetypes (single claim, thread/reply, quote-tweet,
link-list) + one dense article + the DSpark PDF. Output lands in a scratch
folder that mimics the vault layout — the real vault is never touched (the
Config is constructed with vault_path=<scratch>, and ingest.assert_writable
holds the boundary as usual).

Manual, y/N-gated, never crond. Makes real fetches + real LLM calls (that's
the point — you're grading the prompt), so ANTHROPIC_API_KEY must be set.

Usage:
    uv run python scripts/pilot_ingest.py <sends.txt> [scratch_dir]

`sends.txt` is one Telegram-style send per line — a bare URL, `note + URL`,
or pasted prose — e.g.:

    https://x.com/someone/status/111                      # single-claim tweet
    https://x.com/someone/status/222                      # thread/reply
    https://x.com/someone/status/333                      # quote-tweet
    https://x.com/someone/status/444                      # link-list tweet
    https://example.com/dense-essay                       # dense article
    https://example.com/dspark.pdf                        # the DSpark PDF

Blank lines and `#`-prefixed lines are skipped; an inline ` # comment` tail
is stripped. The scratch dir defaults to `data/pilot-scratch/BowerBird`.

Full stock drain is a separate, later step — NOT this script.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bower_bird import app  # noqa: E402
from bower_bird.config import Config  # noqa: E402
from bower_bird.state import State  # noqa: E402


def read_sends(path: Path) -> list[str]:
    sends = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            sends.append(line)
    return sends


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    sends = read_sends(Path(sys.argv[1]))
    scratch = Path(
        sys.argv[2] if len(sys.argv) > 2 else REPO_ROOT / "data" / "pilot-scratch"
    )
    vault = scratch / "BowerBird"
    vault.mkdir(parents=True, exist_ok=True)

    print(f"pilot: {len(sends)} send(s) → scratch vault {vault}")
    for s in sends:
        print(f"  - {s[:100]}")
    print(
        "\nThis makes real fetches and real LLM calls. The real vault is NOT touched."
    )
    if input("Run the pilot? [y/N] ").strip().lower() != "y":
        print("aborted.")
        return 0

    config = Config(
        BOWER_VAULT_PATH=str(vault),
        BOWER_STATE_PATH=str(scratch / "state.json"),
    )
    state = State.load(config.state_path)

    for send in sends:
        try:
            receipt = app.handle_update(config, state, send, "pilot")
        except Exception as exc:  # noqa: BLE001 — one bad doc must not end the pilot
            receipt = f"ERROR: {exc}"
        state.save()
        print(f"\n>>> {send[:100]}\n    {receipt.replace(chr(10), chr(10) + '    ')}")

    print(f"\npilot done — eyeball the nodes under {vault / 'brain' / 'sources'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
