"""Pydantic data contracts that cross a boundary: router output, LLM
structured output. Stateful classes with behavior (e.g. `ReviewStore`) stay in
their own module — this file is contracts only.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# router.py — how a Telegram send was routed
# --------------------------------------------------------------------------- #


class Lane(StrEnum):
    SHELVE = "shelve"  # any link (bare or +note) — becomes a source node now
    PASTE = "paste"  # pasted prose, no link — becomes a source node, sender as author
    TOOL = "tool"  # keep-for-later shelf (a plugin/repo/tool), not knowledge
    NO_LINK = "no_link"  # genuinely unprocessable (empty / junk); parked, not dropped


class Parsed(BaseModel):
    model_config = ConfigDict(frozen=True)

    lane: Lane
    url: str | None
    note: str  # the user's surrounding note ("why") / seed thought / pasted text


# --------------------------------------------------------------------------- #
# llm.py — synthesize_clipping structured output
# --------------------------------------------------------------------------- #


class EntityRef(BaseModel):
    """A named thing mentioned in a source that deserves its own graph node —
    a tool (repo/library/plugin) or a person (author/creator/figure). Filed as
    an unquizzed leaf note, linked to concepts; NOT a bower, never quizzed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="The node title. For a tool, its real name (e.g. "
        "'roboflow/supervision', not 'a CV library'). For a person, their full "
        "name if known, else their handle (e.g. 'Piotr Skalski')."
    )
    url: str = Field(
        default="",
        description="Provenance URL for this entity if one appears in the "
        "supplied links (the repo URL for a tool, the profile/author URL for a "
        "person). Empty string if none is present — never invent one.",
    )
    note: str = Field(
        default="",
        description="One factual line: what this tool IS / who this person is "
        "and why they appear here.",
    )
    topics: list[str] = Field(
        default_factory=list,
        description="Concept handles from the topics list this entity connects "
        "to (e.g. a CV tool → 'Computer vision'). [] if none fit.",
    )


class ClippingPlan(BaseModel):
    """Structured output for shelving a source. Pointers, never a summary."""

    model_config = ConfigDict(extra="forbid")

    concise_title: str = Field(
        description="A short, fluff-free title for this source — the graph node "
        "label. Strip clickbait, subtitles, and '(And the N tricks...)' tails; "
        "keep only the core subject, ideally 2-6 words (e.g. 'The Feynman "
        "Method', not 'The Feynman Method: Why You Forget 90% of What You Read "
        "(And the 4 Prompts That Fix It)'). Keep proper nouns intact."
    )
    description: str = Field(
        description="One factual line: what this source is (type + topic)."
    )
    author: str = Field(
        default="",
        description="The source's author / byline, if the body or note names one "
        "(e.g. 'Paul Graham', 'Jerry Liu'). This is attribution metadata, NOT a "
        "request to make a person node. Empty string if no author is evident — "
        "never guess.",
    )
    category: str = Field(
        default="",
        description="A single top-level category for this source — a short "
        "lowercase noun the graph can group by (e.g. 'ai', 'writing', 'systems', "
        "'biology'). REUSE a category already present in the candidate index "
        "below when one reasonably fits; only coin a new one when none do. This "
        "is the index's grouping column, so keep the vocabulary small.",
    )
    topics: list[str] = Field(
        description="The coarse topic notes this source feeds — a rich source "
        "usually feeds SEVERAL. Each is a SHORT, REUSABLE concept handle: a 2-5 "
        "word noun phrase many sources could link to (e.g. 'Agentic loops', "
        "'Verification in agent loops'), NOT a sentence/claim, NOT the source "
        "title. PREFER the supplied existing candidates; add a new handle only "
        "when no candidate fits and the topic is broad enough to reuse. [] if "
        "none fit."
    )
    key_ideas: list[str] = Field(
        default_factory=list,
        description="3-6 standalone, testable CLAIMS distilled from this source "
        "— each one a reader could be quizzed on, e.g. 'self-attention replaces "
        "recurrence, buying parallelism', never a topic label like 'discusses "
        "attention'. Weight your signal: clipper ==highlights== (what the reader "
        "flagged) outrank a Telegram seed thought, which outranks the body read "
        "on its own terms. Draw ONLY from the supplied body/highlights/seed — "
        "never invented from prior knowledge of the topic. Scale to the source: "
        "a rich essay yields several claims; a single-claim tweet yields one "
        "thin (but still real) claim — thin is fine, don't pad it. A link-list "
        "source may yield few or none. [] if the source is too thin to extract "
        "a real claim.",
    )
    tools: list[EntityRef] = Field(
        default_factory=list,
        description="Tools named in this source — repos, libraries, plugins, "
        "products worth their own shelf node (e.g. 'roboflow/supervision'). "
        "Attach the repo/product URL from the supplied links. [] if none.",
    )
    people: list[EntityRef] = Field(
        default_factory=list,
        description="ONLY the people the reader explicitly tagged with #person "
        "(their mention text is supplied below). Resolve each to a full name and "
        "attach the profile URL from the supplied links. Do NOT add authors or "
        "other names the reader did not tag. [] when no #person tags are given.",
    )
