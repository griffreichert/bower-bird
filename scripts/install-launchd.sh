#!/usr/bin/env bash
# Install (or refresh) the launchd agent that drains bower-bird once a day.
#
# Why launchd, not cron: a cron job missed while the laptop is asleep is gone
# for good. A launchd StartCalendarInterval job that was missed while asleep
# fires on the next wake — exactly the "process when the laptop is awake"
# model bower-bird wants.
#
# Usage: scripts/install-launchd.sh [HOUR] [MINUTE]   (defaults 08:00)
set -euo pipefail

HOUR="${1:-8}"
MINUTE="${2:-0}"

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
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO_ROOT</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$HOUR</integer>
    <key>Minute</key>
    <integer>$MINUTE</integer>
  </dict>
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

printf 'Installed %s — daily drain at %02d:%02d\n' "$LABEL" "$HOUR" "$MINUTE"
printf 'Plist: %s\n' "$PLIST"
printf 'Test now:  launchctl start %s   (then tail data/launchd.log)\n' "$LABEL"
