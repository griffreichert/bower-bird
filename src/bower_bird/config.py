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

from pathlib import Path
from typing import ClassVar

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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


class Config(BaseSettings):
    """Runtime config — secrets/paths/tunables from the environment (or .env).

    The Anthropic SDK reads ANTHROPIC_API_KEY itself, so it is not stored here.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Two-model pipeline, declared in-package (NOT env-configurable — it's design,
    # not deployment). `pull` (link identification) is cheap Haiku. `build`
    # (synthesize_clipping: linking + placement + extraction) runs on Sonnet —
    # the linking quality is what makes the graph worth reading, and build is
    # manual so the cost is triggered deliberately.
    model: ClassVar[str] = "claude-haiku-4-5"
    build_model: ClassVar[str] = "claude-sonnet-5"

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    # Comma/space-separated Telegram chat_ids allowed to drive the bot. A
    # Telegram bot is publicly addressable by @handle, so an empty allowlist
    # means ANY sender can trigger fetches/LLM calls/vault writes — set this to
    # your own chat_id(s) to lock the bot to you. See allowed_chat_id_set.
    allowed_chat_ids: str = Field(default="", alias="BOWER_ALLOWED_CHAT_IDS")
    vault_path: Path = Field(
        default=None, alias="BOWER_VAULT_PATH", validate_default=True
    )
    state_path: Path = Field(
        default=REPO_ROOT / "data" / "state.json", alias="BOWER_STATE_PATH"
    )
    fetch_timeout: float = Field(default=15, alias="BOWER_FETCH_TIMEOUT")
    queue_limit: int = Field(default=100, alias="BOWER_QUEUE_LIMIT")
    # `bb prune` only offers archived husks older than this. The window exists to
    # eyeball thin extractions before the cold original is gone (build can starve
    # on thin bodies) — don't drop it to 0.
    archive_ttl_days: int = Field(default=30, alias="BOWER_ARCHIVE_TTL_DAYS")

    @field_validator("vault_path", mode="before")
    @classmethod
    def _resolve_vault(cls, v: Path | str | None) -> Path:
        vault = Path(v).expanduser() if v else _default_vault_path()
        if vault is None:
            raise ValueError(
                "Could not resolve the vault path. Set BOWER_VAULT_PATH, or "
                "create the notes/ symlink pointing into the Obsidian vault."
            )
        if not vault.is_dir():
            raise ValueError(f"Vault path does not exist: {vault}")
        return vault

    @property
    def allowed_chat_id_set(self) -> set[int]:
        """Telegram chat_ids permitted to drive the bot (parsed from
        allowed_chat_ids). Empty = unlocked: any sender is accepted. Non-empty =
        every other sender is silently dropped in the pull loop."""
        return {int(x) for x in self.allowed_chat_ids.replace(",", " ").split()}

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
    def people_dir(self) -> Path:
        """People notes: brain/people/<name>.md. Unquizzed leaf nodes (authors,
        creators, figures) — linked to concepts, never minted as bowers."""
        return self.brain_dir / "people"

    @property
    def tools_dir(self) -> Path:
        """Tool notes: brain/tools/<name>.md. Unquizzed leaf nodes (repos, libs,
        plugins referenced in a read clip) — linked to concepts, never quizzed.
        Distinct from the flat `tools.md` shelf (the bare `tool:` recall lane)."""
        return self.brain_dir / "tools"

    @property
    def index_path(self) -> Path:
        """The graph catalog — one line per brain/ page (`[[title]] · category ·
        one-liner`). `build` upserts it incrementally (O(1) per capture) and
        reads it as the sole link-candidate lookup, so build cost stays bounded
        regardless of graph size. `weave` does periodic full repair."""
        return self.brain_dir / "_index.md"

    @property
    def log_path(self) -> Path:
        """Append-only activity log — one line per build (Karpathy's log.md).
        `weave` reads it to run incrementally (touch only what changed since the
        last pass) instead of rescanning the whole graph."""
        return self.brain_dir / "_log.md"

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
    """Load config from the environment / .env. Raises RuntimeError with a
    readable message on a missing token or unresolvable vault path."""
    try:
        return Config()
    except ValidationError as exc:
        raise RuntimeError(str(exc)) from exc
