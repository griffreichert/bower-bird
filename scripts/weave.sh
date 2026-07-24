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
  'from bower_bird.config import Config; print(Config().vault_path)' \
  | tail -1)"

if [ ! -d "$VAULT" ]; then
  echo "Vault folder not found: $VAULT" >&2
  exit 1
fi

cd "$VAULT"

# Weave runs on the Max SUBSCRIPTION, not the API. A shell-exported
# ANTHROPIC_API_KEY (from .env, for the Tier-1 Haiku path) takes precedence over
# the claude.ai login and would silently bill API tokens — unset it here so
# `claude` falls back to the subscription auth. This is the design invariant:
# "no API tokens for whole-graph synthesis."
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN

PROMPT="Run the weave pass following BowerBird/CLAUDE.md: deep whole-graph synthesis + lint over the existing graph."

# First run must be INTERACTIVE: headless `claude -p` cannot display the
# folder-write permission prompt, so it silently hangs. Gate on a sentinel
# (gitignored) — first time, launch an interactive session seeded with the
# prompt so you can approve write access inline; mark it, then all future runs
# go headless.
SENTINEL="$REPO_ROOT/.weave-approved"
if [ ! -f "$SENTINEL" ]; then
  echo "First weave run — launching interactively so you can grant write access"
  echo "to $VAULT. Approve the folder-write prompt (choose 'don't ask again for"
  echo "this folder'), let the pass finish, then exit."
  echo
  claude "$PROMPT"
  touch "$SENTINEL"
  echo
  echo "Marked approved ($SENTINEL) — future 'make weave' runs headless."
  exit 0
fi

# ponytail: prompt finalized in Phase 2 alongside the weave/lint playbook rewrite
# in BowerBird/CLAUDE.md. For now it points Claude Code at that playbook.
# WEAVE_MODEL pins the model (e.g. WEAVE_MODEL=opus) — the saved default may be
# a pricier tier than a long headless grind deserves.
exec claude ${WEAVE_MODEL:+--model "$WEAVE_MODEL"} -p "$PROMPT"
