#!/usr/bin/env bash
# Weave: deep whole-graph synthesis + lint via Claude Code (subscription).
#
# This is the whole-graph pass — deliberately run by hand (or from a session),
# NOT crond: driving the subscription from an unattended loop is a ToS gray area
# and burns usage caps. The cheap automated work is the `pull` Haiku pass (cron);
# this is the expensive, in-the-loop thinking.
#
# First run: do it interactively once (`cd <vault> && claude`) so you can grant
# write permission to the folder; after that this wrapper is a one-shot.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VAULT="$(cd "$REPO_ROOT" && uv run python -c \
  'from bower_bird.config import load_config; print(load_config().vault_path)' \
  | tail -1)"

if [ ! -d "$VAULT" ]; then
  echo "Vault folder not found: $VAULT" >&2
  exit 1
fi

cd "$VAULT"
# ponytail: prompt finalized in Phase 2 alongside the weave/lint playbook rewrite
# in BowerBird/CLAUDE.md. For now it points Claude Code at that playbook.
exec claude -p \
  "Run the weave pass following BowerBird/CLAUDE.md: deep whole-graph synthesis + lint over the existing graph."
