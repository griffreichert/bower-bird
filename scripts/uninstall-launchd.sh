#!/usr/bin/env bash
# Remove the bower-bird launchd agent.
set -euo pipefail

LABEL="com.griffinreichert.bower-bird"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
else
  echo "Not installed: $PLIST"
fi
