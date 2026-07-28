"""Configuration: environment + vault paths + model.

Loaded once at startup via `Config()`. ANTHROPIC_API_KEY is loaded from .env
and exported to the process environment for the Anthropic SDK — launchd jobs
don't inherit shell exports (see model_post_init).

The vault is *not* part of this repo. It lives in iCloud. bower-bird owns one
folder there, `BowerBird/`, and writes nowhere else (per INVARIANTS). That
owned folder is `vault_path`. We locate it from the gitignored `notes/` symlink
(notes/ -> G/g/projects/bower-bird): the Obsidian root (.../G) is three parents
up from that target, and the owned folder is `<root>/BowerBird`. Override with
BOWER_VAULT_PATH.

Writable paths at runtime: inbox/ (clipper drop target), brain/ (sources/,
bowers/, people/, tools/), archive/, to-clip.md, tools.md, _inbox.md, digests/.
"""

import os
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# The single vault folder bower-bird owns and writes into.
OWNED_FOLDER = "BowerBird"


class LLMSettings(BaseModel):
    """LLM tunables: model names + per-call max-token caps.

    Design, not deployment — not env-configurable. Haiku-only for every Tier-1
    lane, per-item and per-clip: `model` covers link identification AND
    synthesize_clipping (linking + placement + extraction) for everything
    Telegram-facing. `paper_model` is a single per-lane override — the PDF
    lane alone runs synthesize_clipping on Sonnet, because Haiku thins out
    extracting from 40 dense pages; every other lane stays cheap and
    always-on.

    Haiku 4.5 does not take `thinking`/`effort` params — omit them. Small caps:
    these are one-liners and short JSON, not essays.
    """

    model_config = ConfigDict(frozen=True)

    model: str = Field(
        default="claude-haiku-4-5",
        description="Tier-1 model: link identification + synthesize_clipping "
        "for every lane except the PDF lane.",
    )
    paper_model: str = Field(
        default="claude-sonnet-5",
        description="PDF lane's synthesize_clipping model. Haiku thins out on "
        "long dense PDFs (papers, reports); Sonnet handles the long-context "
        "extraction. Used ONLY for the bare-PDF-link lane.",
    )
    describe_max_tokens: int = Field(
        default=120, gt=0, description="Cap for describe_link's one-line output."
    )
    clipping_max_tokens: int = Field(
        default=1600,
        gt=0,
        description="Cap for synthesize_clipping's structured plan. Sized for "
        "detailed key-idea claims (mechanism/numbers, not headlines).",
    )
    judge_max_tokens: int = Field(
        default=300, gt=0, description="Cap for judge_answer's verdict + rationale."
    )
    question_max_tokens: int = Field(
        default=200, gt=0, description="Cap for generate_question's quiz question."
    )
    topic_relevance_limit: int = Field(
        default=40,
        gt=0,
        description="Max concept titles selected by relevance to the source "
        "being shelved — the query-matched half of candidate_index's "
        "two-part list. Popularity alone (topic_candidate_limit) buries "
        "low-count-but-on-topic concepts below the cut (measured 2026-07-28: "
        "the Harvey source's actually-relevant concepts, e.g. 'Data "
        "extraction', all sat at feeding-source count 1, well below the "
        "popularity cutoff).",
    )
    topic_candidate_limit: int = Field(
        default=30,
        gt=0,
        description="Max concept titles in candidate_index's popularity half "
        "— the always-present backbone, ranked by feeding-source count. Not "
        "the whole candidate list: topic_relevance_limit supplies the "
        "query-relevant half ahead of it. The full ~320-title index buries "
        "the 'prefer an existing title' instruction and Haiku coins new ones "
        "instead (measured: 221 singleton topics) — capping to the "
        "most-fed concepts keeps the preferred targets small enough to "
        "matter.",
    )


class Config(BaseSettings):
    """Runtime config — secrets/paths/tunables from the environment (or .env).

    ANTHROPIC_API_KEY rides along from .env for launchd runs (see
    model_post_init).
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        frozen=True,
    )

    # LLM tunables (model names, max-token caps) — design, not deployment;
    # not env-configurable. See LLMSettings.
    llm: ClassVar[LLMSettings] = LLMSettings()

    telegram_bot_token: SecretStr = Field(alias="TELEGRAM_BOT_TOKEN")
    # The Anthropic SDK reads ANTHROPIC_API_KEY from the process environment —
    # which a launchd job does NOT inherit from the shell. Load it from .env
    # here and export it in model_post_init so cron runs authenticate too.
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""), alias="ANTHROPIC_API_KEY"
    )
    # Telegram chat_ids allowed to drive the bot, parsed from a comma/space-
    # separated env string. A Telegram bot is publicly addressable by @handle,
    # so an empty allowlist means ANY sender can trigger fetches/LLM calls/
    # vault writes — set this to your own chat_id(s) to lock the bot to you.
    allowed_chat_ids: set[int] = Field(
        default_factory=set, alias="BOWER_ALLOWED_CHAT_IDS"
    )
    vault_path: Path = Field(
        default=None, alias="BOWER_VAULT_PATH", validate_default=True
    )
    state_path: Path = Field(
        default=REPO_ROOT / "data" / "state.json", alias="BOWER_STATE_PATH"
    )
    fetch_timeout: float = Field(
        default=15,
        gt=0,
        alias="BOWER_FETCH_TIMEOUT",
        description="HTTP fetch timeout, seconds.",
    )
    queue_limit: int = Field(
        default=100,
        ge=1,
        alias="BOWER_QUEUE_LIMIT",
        description="Max Telegram updates pulled per run.",
    )
    # `bb prune` only offers archived husks older than this. The window exists to
    # eyeball thin extractions before the cold original is gone (build can starve
    # on thin bodies) — don't drop it to 0.
    archive_ttl_days: int = Field(default=30, ge=0, alias="BOWER_ARCHIVE_TTL_DAYS")

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def _parse_chat_ids(cls, v: str | int | set[int] | None) -> set[int]:
        # env boundary: pydantic-settings json-parses "8504780905" to a bare
        # int before this runs, so a single-id allowlist arrives as int.
        if v is None:
            return set()
        if isinstance(v, int):
            return {v}
        if isinstance(v, str):
            return {int(x) for x in v.replace(",", " ").split()}
        return v

    @field_validator("vault_path", mode="before")
    @classmethod
    def _resolve_vault(cls, v: Path | str | None) -> Path:
        vault = Path(v).expanduser() if v else None
        if vault is None:
            # Resolve bower-bird's owned folder from the `notes/` symlink:
            # notes/ -> .../G/g/projects/bower-bird, so the Obsidian root
            # (.../G) is parents[2] of the resolved target, and the owned
            # folder is `<root>/BowerBird`.
            link = REPO_ROOT / "notes"
            try:
                target = link.resolve(strict=True)
            except (OSError, RuntimeError):
                target = None
            if target is not None and len(target.parents) >= 3:
                vault = target.parents[2] / OWNED_FOLDER
        if vault is None:
            raise ValueError(
                "Could not resolve the vault path. Set BOWER_VAULT_PATH, or "
                "create the notes/ symlink pointing into the Obsidian vault."
            )
        if not vault.is_dir():
            raise ValueError(f"Vault path does not exist: {vault}")
        return vault

    # --- Derived locations inside the owned folder (everything we touch) ---
    @property
    def inbox_dir(self) -> Path:
        """Web Clipper transient drop target — the pull consumes it within one
        tick (antilibrary model: everything sent is shelved, never queued)."""
        return self.vault_path / "inbox"

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
        """Recycle bin: processed clip originals, moved here (never
        hard-deleted; `bb prune` GCs it after the TTL). Since #25 the source
        node inlines the body — archive/ carries no unique knowledge."""
        return self.vault_path / "archive"

    @property
    def review_path(self) -> Path:
        """Spaced-rep review state for peck — keyed by bower id.

        Lives in the vault (not the repo) so it stays close to the bowers it
        tracks and is not accidentally version-controlled. Distinct from the
        repo-side state.json (which is idempotency-only).
        """
        return self.vault_path / "_review.json"

    def model_post_init(self, __context: object) -> None:
        """Export ANTHROPIC_API_KEY into the process environment when it isn't
        already there — launchd jobs don't inherit the shell's exports, and
        the Anthropic SDK only looks in the environment."""
        key = self.anthropic_api_key.get_secret_value()
        if key:
            os.environ.setdefault("ANTHROPIC_API_KEY", key)
