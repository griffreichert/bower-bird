"""Pydantic data contracts that cross a boundary: router output, LLM
structured output. Stateful classes with behavior (e.g. `ReviewStore`) stay in
their own module — this file is contracts only.
"""

from enum import StrEnum
from typing import Literal

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
# fetch.py — page metadata (title/description/excerpt)
# --------------------------------------------------------------------------- #


class PageMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    description: str
    body_excerpt: str  # only used by the learned lane
    author: str = ""  # byline, when the page/clip exposes one

    @property
    def is_thin(self) -> bool:
        """Fetch came back empty — no real title and no description. The page is
        likely JS-/login-walled; route to the clip queue instead of writing a
        broken inbox doc."""
        return not self.description and (not self.title or self.title == self.url)


# --------------------------------------------------------------------------- #
# resolve.py — a resolved tweet's text (+ reply hint / quote / article)
# --------------------------------------------------------------------------- #


class TweetText(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    url: str  # canonical: https://x.com/{handle}/status/{id}
    author_handle: str  # screen_name, no @
    author_name: str
    text: str
    quoted_handle: str = ""  # set when the tweet quotes another
    quoted_text: str = ""
    in_reply_to: str = ""  # screen_name this tweet replies to ("" if not a reply)
    article_title: str = ""  # set when the tweet wraps a native X long-form Article
    article_body: str = ""  # article content, rendered to markdown


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
        max_length=64,
        description="A short, fluff-free title for this source — the graph node "
        "label. HARD RULE: 2-6 words. NEVER reuse the source's own title when "
        "it is longer than that — compress it to the core subject (e.g. "
        "'Agents need bash', not 'Long-running agents don't need tools or "
        "hosted sandboxes; they need bash'). Strip clickbait, subtitles, and "
        "'(And the N tricks...)' tails. Keep proper nouns intact.",
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
        "attention'. Make each claim DETAILED enough to relearn the idea "
        "without reopening the source: include the mechanism, reasoning, or "
        "numbers that make it stick, not just the headline. Weight your "
        "signal: clipper ==highlights== (what the reader "
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


# --------------------------------------------------------------------------- #
# llm.py — generate_question / judge_answer structured output
# --------------------------------------------------------------------------- #


class QuizQuestion(BaseModel):
    """A freshly-generated `peck` question for one source node (#18).

    Generated at quiz time (never stored) so a node's question doesn't
    degrade into recognition after a few reps. Depth scales with the
    learner's Leitner box — see `llm.generate_question`.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        description="A single quiz question, box-appropriate depth, grounded "
        "only in the supplied key ideas (+ seed thought / linked titles)."
    )


class JudgeVerdict(BaseModel):
    """LLM-as-judge grade for one `peck` recall answer.

    Grades the learner's typed answer against the node's key ideas (+ seed
    thought) as ground truth, so the quiz loop is a real eval loop, not
    self-assessment. The full node body is never sent — only the distilled
    key ideas (#18).
    """

    model_config = ConfigDict(extra="forbid")

    grade: Literal["strong", "weak", "wrong"] = Field(
        description="strong = captures the load-bearing idea, essentially "
        "correct; weak = partially right but vague on or missing the core point; "
        "wrong = incorrect, or a non-answer (blank / 'I don't know')."
    )
    rationale: str = Field(
        description="One or two lines addressed to the learner: what they nailed "
        "and what they missed. Grade the understanding, not the wording."
    )


# --------------------------------------------------------------------------- #
# review.py — spaced-rep review state (ReviewStore, the stateful class, stays
# in review.py; these are the frozen per-node/per-event data shapes it holds)
# --------------------------------------------------------------------------- #


class ReviewEntry(BaseModel):
    """One past review event — stored in the history list."""

    model_config = ConfigDict(extra="forbid")

    reviewed_on: str = Field(description="ISO date of the review.")
    grade: Literal["strong", "weak", "wrong"] = Field(
        description="strong | weak | wrong."
    )
    box_before: int = Field(description="Box the node was in before this review.")
    box_after: int = Field(description="Box it moved to after grading.")


class Review(BaseModel):
    """Per-node spaced-rep state, keyed by the source node's immutable id."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Source node id — immutable, matches frontmatter id:.")
    due: str = Field(
        description="ISO date when next review is due (due <= today → quiz it)."
    )
    box: int = Field(
        default=0,
        description="Current Leitner box (0 = just minted / wrong, max = 5).",
    )
    last_grade: Literal["strong", "weak", "wrong"] | None = Field(
        default=None,
        description="Grade from the most recent review, or None if never reviewed.",
    )
    reviews: list[ReviewEntry] = Field(
        default_factory=list,
        description="Full grading history, oldest first.",
    )
    taught: bool = Field(
        default=False,
        description="True once the teach-first card has been shown at least "
        "once. Graduates the card out of teach so it quizzes next session, "
        "even before it has a graded review.",
    )
    retired: bool = Field(
        default=False,
        description="Retired via the 'd' grading letter — excluded from due "
        "forever, stays in the store/census.",
    )
    bad_streak: int = Field(
        default=0,
        description="Consecutive 'bad question' flags with no intervening real "
        "grade. Reset to 0 by any real grade. >= BAD_STREAK_FLAG flags the "
        "node's key ideas as thin (weave fodder).",
    )

    @property
    def is_teach(self) -> bool:
        """Teach-first card: show key ideas, no question/judge/grade. True until
        the card has been taught once (``taught``) or graded (``reviews``)."""
        return not self.reviews and not self.taught
