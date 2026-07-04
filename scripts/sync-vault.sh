#!/usr/bin/env bash
# Copy the canonical vault docs (vault/CLAUDE.md, vault/README.md) from the repo
# into the owned BowerBird/ folder. The repo is the single source of truth —
# never hand-edit the copies in the vault (they get overwritten here).
#
# Drift between these docs and the code is guarded by tests/test_vault_docs.py
# (every owned path in config.py must be named in CLAUDE.md). Run `make test`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Use the venv interpreter directly, not `uv run`: uv re-hides the editable
# `.pth` (UF_HIDDEN) on every sync, which breaks `import bower_bird`.
VAULT="$("$REPO_ROOT/.venv/bin/python" -c \
  'from bower_bird.config import load_config; print(load_config().vault_path)' \
  | tail -1)"

if [ ! -d "$VAULT" ]; then
  echo "Vault folder not found: $VAULT" >&2
  exit 1
fi

for f in CLAUDE.md README.md; do
  cp "$REPO_ROOT/vault/$f" "$VAULT/$f"
  echo "synced vault/$f → $VAULT/$f"
done
