#!/usr/bin/env bash
# demo-record.sh — one-run runbook for the BowerBird demo GIF (Option B: live vault).
#
# Records a real ≤90s asciinema cast of the loop — capture → note lands → peck
# (LLM-as-judge) — then renders it to assets/demo.gif with agg.
#
# This script does NOT fake anything: asciinema captures YOUR live terminal
# session. You send the Telegram link, type the peck answer, and accept/override
# the judge. It only sets the stage and echoes the beats.
#
# Prereqs (human, one-time):
#   brew install asciinema agg
#   - Do the privacy pass in notes/2026-07-17-demo-spec.md FIRST.
#   - Stage ONE judged card due today (see notes/2026-07-17-lint-proposed.md):
#       bb peck --list-due && bb peck --grade <SAFE_ID> weak && bb touch <SAFE_ID>
#   - Pre-send your chosen safe link from your phone BEFORE running this.
#
# Usage:  scripts/demo-record.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# Live config: bower-bird resolves the vault from the notes/ symlink by default.
# Respect BOWER_VAULT_PATH if you've set it; otherwise the default (live) config
# is used — no hardcoded iCloud path (this repo is public).
: "${BOWER_VAULT_PATH:=}"

cast="demo.cast"
gif="assets/demo.gif"

for tool in asciinema agg; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: '$tool' not found. Run: brew install asciinema agg" >&2
    exit 1
  fi
done
mkdir -p assets

cat <<'BEATS'
────────────────────────────────────────────────────────────
  BowerBird demo — 3 beats, ≤90s. Recording starts on Enter.
  (bb lint is intentionally CUT — it lists node titles, a leak.)

  beat 1  capture   (~15s):  bb pull       → "processed 1 item(s)."
  beat 2  note lands (~20s):  bat brain/sources/<your-safe-node>.md
  beat 3  peck      (~45s):  bb peck       → answer in your words,
                             then accept/override the LLM judge grade.
  Ctrl-D to stop the recording.
────────────────────────────────────────────────────────────
BEATS
read -r -p "Press Enter to start recording (Ctrl-C to abort)… " _

# Overwrite a previous take rather than erroring out.
asciinema rec --overwrite "$cast"

echo
echo "Recorded $cast. Rendering GIF…"
# Tune legibility for GitHub inline render; adjust if text is cramped.
agg --cols 90 --rows 28 --font-size 20 "$cast" "$gif"

echo "→ $gif ready. Preview it, confirm ≤90s and no private content, then:"
echo "    git add assets/demo.gif        # (optionally: git add $cast)"
echo "    # uncomment the embed in README.md, then commit."
