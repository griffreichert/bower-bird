from pathlib import Path
from typing import ClassVar, Literal

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

    Haiku 5.5 thinks by default and the thinking counts toward `max_tokens`,
    so the Haiku calls pass `effort` (low) and every cap leaves room for it.
    """

    model_config = ConfigDict(frozen=True)

    model: str = Field(
        default="claude-haiku-5-5",
        description="Tier-1 model: link identification + synthesize_clipping "
        "for every lane except the PDF lane.",
    )
    paper_model: str = Field(
        default="claude-sonnet-5",
        description="PDF lane's synthesize_clipping model. Haiku thins out on "
        "long dense PDFs (papers, reports); Sonnet handles the long-context "
        "extraction. Used ONLY for the bare-PDF-link lane.",
    )
    effort: Literal["low", "medium", "high"] = Field(
        default="low",
        description="`output_config.effort` for every `model` call. Haiku 5.5 "
        "defaults to medium; low keeps thinking short on one-liners and short "
        "JSON. The PDF lane's `paper_model` keeps its own default.",
    )
    describe_max_tokens: int = Field(
        default=1024,
        gt=0,
        description="Cap for describe_link: thinking plus a one-line output.",
    )
    clipping_max_tokens: int = Field(
        default=1600,
        gt=0,
        description="Cap for synthesize_clipping's structured plan. Sized for "
        "detailed key-idea claims (mechanism/numbers, not headlines).",
    )
    judge_max_tokens: int = Field(
        default=1024,
        gt=0,
        description="Cap for judge_answer: thinking plus verdict and rationale.",
    )
    question_max_tokens: int = Field(
        default=1024,
        gt=0,
        description="Cap for generate_question: thinking plus the question.",
    )
    card_max_tokens: int = Field(
        default=1600,
        gt=0,
        description="Cap for write_cards' plan — one question + short answer "
        "per claim, for every claim on a source.",
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
        "— the always-present backbone, ranked by inbound-link count "
        "(sources + tool/people leaves). Not "
        "the whole candidate list: topic_relevance_limit supplies the "
        "query-relevant half ahead of it. The full ~320-title index buries "
        "the 'prefer an existing title' instruction and Haiku coins new ones "
        "instead (measured: 221 singleton topics) — capping to the "
        "most-fed concepts keeps the preferred targets small enough to "
        "matter.",
    )


class Config(BaseSettings):
    """Runtime config — secrets/paths/tunables from the environment (or .env).

    The Anthropic key lives under BOWER_BIRD_ANTHROPIC_API_KEY and is handed
    to the SDK client directly, so it never becomes the ANTHROPIC_API_KEY
    default that Claude Code and other tools read.
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
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""),
        alias="BOWER_BIRD_ANTHROPIC_API_KEY",
        description="Passed to the SDK client in llm.anthropic_client, never "
        "exported to the environment.",
    )
    allowed_chat_ids: set[int] = Field(
        default_factory=set,
        alias="BOWER_ALLOWED_CHAT_IDS",
        description="Telegram chat ids allowed to drive the bot, comma or space "
        "separated. Empty lets ANY sender trigger fetches, LLM calls and vault "
        "writes, since a bot is publicly addressable by handle.",
    )
    vault_path: Path = Field(
        default=None, alias="BOWER_VAULT_PATH", validate_default=True
    )
    anki_url: str = Field(
        default="http://127.0.0.1:8765",
        alias="BOWER_ANKI_URL",
        description="AnkiConnect endpoint (add-on 2055492159); only reachable "
        "while the Anki desktop app is open.",
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
    archive_ttl_days: int = Field(
        default=30,
        ge=0,
        alias="BOWER_ARCHIVE_TTL_DAYS",
        description="`bb prune` only offers husks older than this. The window "
        "leaves time to spot a thin extraction while the original still exists.",
    )

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
            # notes/ -> <vault>/g/projects/bower-bird, so the vault is parents[2].
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
        """Web Clipper drop target; the pull consumes it within one tick."""
        return self.vault_path / "inbox"

    @property
    def telegram_inbox_path(self) -> Path:
        """Catch-all log for Telegram messages we couldn't process."""
        return self.vault_path / "_inbox.md"

    @property
    def to_clip_path(self) -> Path:
        """Queue of links httpx cannot read, for a human to Web Clip.

        Each one is opened in a browser and clipped into inbox/, where the clip
        lane shelves it.
        """
        return self.vault_path / "to-clip.md"

    @property
    def tools_path(self) -> Path:
        """Shelf of tools collected via `tool:`; never enters brain/."""
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
        """Concept note root, brain/bowers/.

        Notes live in topical subfolders, so callers recurse. New concepts land
        flat here until weave files them. Human text here is append only.
        """
        return self.brain_dir / "bowers"

    @property
    def people_dir(self) -> Path:
        """People leaf notes, linked to concepts and never quizzed."""
        return self.brain_dir / "people"

    @property
    def tools_dir(self) -> Path:
        """Tool leaf notes named inside sources, never quizzed.

        Distinct from the flat `tools.md` shelf that the `tool:` lane fills.
        """
        return self.brain_dir / "tools"

    @property
    def index_path(self) -> Path:
        """Graph catalog, one ``[[title]] · category · one liner`` line per page.

        Capture upserts one line at a time and reads it as the only link
        candidate lookup, so capture cost stays bounded as the graph grows.
        `bb tidy` rebuilds it in full.
        """
        return self.brain_dir / "_index.md"

    @property
    def log_path(self) -> Path:
        """Append only activity log, one line per capture.

        Weave reads it to work only on what changed since its last pass.
        """
        return self.brain_dir / "_log.md"

    @property
    def archive_dir(self) -> Path:
        """Recycle bin of processed clip originals, cleared by `bb prune`.

        Source nodes inline the full body, so archive/ holds no unique
        knowledge.
        """
        return self.vault_path / "archive"

    @property
    def recall_map_path(self) -> Path:
        """Anki recall rolled up by concept, rebuilt whole by `bb anki harvest`.

        Derived and bot owned like `_index.md`; it never holds human prose.
        """
        return self.vault_path / "digests" / "recall-map.md"

    @property
    def review_path(self) -> Path:
        """Spaced-rep review state for peck — keyed by bower id.

        Lives in the vault (not the repo) so it stays close to the bowers it
        tracks and is not accidentally version-controlled. Distinct from the
        repo-side state.json (which is idempotency-only).
        """
        return self.vault_path / "_review.json"
