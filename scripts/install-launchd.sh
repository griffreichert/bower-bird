#!/usr/bin/env bash
# Install (or refresh) the launchd agent that runs bower-bird every ~30 min.
#
# Why launchd, not cron: cron jobs missed while the laptop is asleep are gone
# for good. StartInterval jobs that were missed while asleep fire on the next
# wake — exactly the "process when the laptop is awake" model bower-bird wants.
#
# One pass does two things:
# Runs `pull` only: pull queue → fetch+render → fill inbox/ (+ tool:/to-clip
# routing). Cheap Haiku capture.
#
# build/weave/peck/forage are NOT run by this job — they remain manual, so their
# (Sonnet / subscription) cost is triggered deliberately, not on a timer.
#
# Usage: scripts/install-launchd.sh [INTERVAL_SECONDS]   (default: 900 = 15 min)
set -euo pipefail

INTERVAL="${1:-900}"

LABEL="com.griffinreichert.bower-bird"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_BIN="$(command -v uv)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV_BIN</string>
    <string>run</string>
    <string>python</string>
    <string>-m</string>
    <string>bower_bird</string>
    <string>pull</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO_ROOT</string>
  <key>StartInterval</key>
  <integer>$INTERVAL</integer>
  <key>StandardOutPath</key>
  <string>$REPO_ROOT/data/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>$REPO_ROOT/data/launchd.err</string>
</dict>
</plist>
PLIST_EOF

# Reload cleanly (ignore "not loaded" on first install).
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

INTERVAL_MIN=$(( INTERVAL / 60 ))
printf 'Installed %s — pull every %d min (%ds)\n' "$LABEL" "$INTERVAL_MIN" "$INTERVAL"
printf 'Plist: %s\n' "$PLIST"
printf 'Test now:  launchctl start %s   (then tail data/launchd.log)\n' "$LABEL"
