"""Configuration: environment + vault paths + model.

Loaded once at startup. The Anthropic SDK reads ANTHROPIC_API_KEY from the
environment itself, so it is not stored here.

The vault is *not* part of this repo. It lives in iCloud and is reachable via
the gitignored `notes/` symlink (notes/ -> G/g/projects/bower-bird). The vault
root is three parents up from that target. Override with BOWER_VAULT_PATH.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Cheap by design (capture + one daily digest); token budget is not a concern
# here, so the smallest capable model is the right default. See INVARIANTS:
# "downscale the model", "session-budget aware".
DEFAULT_MODEL = "claude-haiku-4-5"

REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_vault_path() -> Path | None:
    """Resolve the vault root from the `notes/` symlink, if present.

    notes/ -> .../G/g/projects/bower-bird, so the vault root (.../G) is
    parents[2] of the resolved target.
    """
    link = REPO_ROOT / "notes"
    try:
        target = link.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if len(target.parents) >= 3:
        return target.parents[2]
    return None


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    vault_path: Path
    model: str
    state_path: Path
    fetch_timeout: float
    drain_limit: int

    # --- Derived vault locations (the only places we may write at runtime) ---
    @property
    def clippings_dir(self) -> Path:
        return self.vault_path / "Clippings"

    @property
    def reading_list_path(self) -> Path:
        return self.vault_path / "g" / "learning" / "reading-list.md"

    @property
    def learning_dir(self) -> Path:
        """Evergreen-note directory. Read-only at runtime: we *propose*
        backlinks into these notes, we never edit them (per INVARIANTS)."""
        return self.vault_path / "g" / "learning"


def load_config() -> Config:
    load_dotenv(REPO_ROOT / ".env")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Get one from @BotFather and put it "
            "in .env or the environment."
        )

    vault_env = os.environ.get("BOWER_VAULT_PATH", "").strip()
    vault_path = Path(vault_env).expanduser() if vault_env else _default_vault_path()
    if vault_path is None:
        raise RuntimeError(
            "Could not resolve the vault path. Set BOWER_VAULT_PATH, or create "
            "the notes/ symlink pointing into the Obsidian vault."
        )
    if not vault_path.is_dir():
        raise RuntimeError(f"Vault path does not exist: {vault_path}")

    state_env = os.environ.get("BOWER_STATE_PATH", "").strip()
    state_path = (
        Path(state_env).expanduser() if state_env else REPO_ROOT / "data" / "state.json"
    )

    return Config(
        telegram_bot_token=token,
        vault_path=vault_path,
        model=os.environ.get("BOWER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        state_path=state_path,
        fetch_timeout=float(os.environ.get("BOWER_FETCH_TIMEOUT", "15")),
        drain_limit=int(os.environ.get("BOWER_DRAIN_LIMIT", "100")),
    )
