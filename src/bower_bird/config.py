"""Configuration: environment + vault paths + model.

Loaded once at startup. The Anthropic SDK reads ANTHROPIC_API_KEY from the
environment itself, so it is not stored here.

The vault is *not* part of this repo. It lives in iCloud. bower-bird owns one
folder there, `BowerBird/`, and writes nowhere else (per INVARIANTS). That
owned folder is `vault_path`. We locate it from the gitignored `notes/` symlink
(notes/ -> G/g/projects/bower-bird): the Obsidian root (.../G) is three parents
up from that target, and the owned folder is `<root>/BowerBird`. Override with
BOWER_VAULT_PATH.

Writable paths at runtime: inbox/, trinkets/, sources/, notes/ (brain/bowers/),
archive/, to-clip.md, tools.md, _inbox.md, digests/.
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

# The single vault folder bower-bird owns and writes into.
OWNED_FOLDER = "BowerBird"


def _default_vault_path() -> Path | None:
    """Resolve bower-bird's owned folder from the `notes/` symlink, if present.

    notes/ -> .../G/g/projects/bower-bird, so the Obsidian root (.../G) is
    parents[2] of the resolved target, and the owned folder is `<root>/BowerBird`.
    """
    link = REPO_ROOT / "notes"
    try:
        target = link.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if len(target.parents) >= 3:
        return target.parents[2] / OWNED_FOLDER
    return None


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    vault_path: Path
    model: str
    state_path: Path
    fetch_timeout: float
    queue_limit: int

    # --- Derived locations inside the owned folder (everything we touch) ---
    @property
    def inbox_dir(self) -> Path:
        """Reading room: bot fills it (rendered clips); humans read + annotate
        here; nothing is auto-processed from this folder (INVARIANT)."""
        return self.vault_path / "inbox"

    @property
    def trinkets_dir(self) -> Path:
        """Read+annotated items awaiting arrangement into brain/bowers/.
        The move inbox/ → trinkets/ is the read signal that authorises graph
        writes. The ingest/gather scan reads from here, never from inbox/."""
        return self.vault_path / "trinkets"

    @property
    def telegram_inbox_path(self) -> Path:
        """Catch-all log for Telegram messages we couldn't process."""
        return self.vault_path / "_inbox.md"

    @property
    def to_clip_path(self) -> Path:
        """Clip queue: links httpx can't read (X/Twitter, JS-walled). Open each
        in a browser, Web Clipper into inbox/, then it gets read + processed."""
        return self.vault_path / "to-clip.md"

    @property
    def tools_path(self) -> Path:
        """Keep-for-later shelf: plugins/repos/tools collected via `tool:`. Not
        knowledge — never enters brain/."""
        return self.vault_path / "tools.md"

    @property
    def brain_dir(self) -> Path:
        """The knowledge layer: distilled nests + raw sources + topic index."""
        return self.vault_path / "brain"

    @property
    def sources_dir(self) -> Path:
        """Raw per-source notes (clip / url + extracted content)."""
        return self.brain_dir / "sources"

    @property
    def notes_dir(self) -> Path:
        """Concept-note root: brain/bowers/. Notes live in topical subfolders
        (bowers), so callers recurse. New concepts land flat here until Tier-2
        files them. Additive-only on human-authored notes (per INVARIANTS)."""
        return self.brain_dir / "bowers"

    @property
    def topic_index_path(self) -> Path:
        """The bower map, maintained by Tier-2 (Claude Code)."""
        return self.brain_dir / "_topic_index.md"

    @property
    def archive_dir(self) -> Path:
        """Processed clipper originals, moved here (never hard-deleted)."""
        return self.vault_path / "archive"

    @property
    def review_path(self) -> Path:
        """Spaced-rep review state for peck — keyed by bower id.

        Lives in the vault (not the repo) so it stays close to the bowers it
        tracks and is not accidentally version-controlled. Distinct from the
        repo-side state.json (which is idempotency-only).
        """
        return self.vault_path / "_review.json"

    @property
    def forage_path(self) -> Path:
        """Gap-signal proposals written by the forage subcommand.

        Propose-never-auto-fill: lists gap topics and search prompts only.
        Nothing in inbox/ is ever written automatically — the human approves
        items by hand. Pull-only; never written from the cron pass.
        """
        return self.vault_path / "forage.md"


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
        queue_limit=int(os.environ.get("BOWER_QUEUE_LIMIT", "100")),
    )
