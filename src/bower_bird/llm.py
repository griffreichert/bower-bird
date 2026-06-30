"""Anthropic calls. First-party API, Haiku by default.

Two jobs, both small and cheap:

- `describe_link`  — one-line "what is it" metadata for the to-read lane.
  Grounded ONLY on page metadata (title/description); never the body. This is
  identification, not a summary — we don't distill unread articles.
- `synthesize_clipping` — for the learned lane (the user has read it and added
  a note), turn note + source into a clipping description and a set of
  *proposed* [[backlinks]] drawn from existing evergreen notes.

The clipping contract is a pydantic model; `messages.parse` derives the JSON
schema from it and validates the response back into the model.
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, ConfigDict, Field

from bower_bird.fetch import PageMeta

# Haiku 4.5 does not take `thinking`/`effort` params — omit them. Small caps:
# these are one-liners and short JSON, not essays.
_DESCRIBE_MAX_TOKENS = 120
_CLIPPING_MAX_TOKENS = 1200


class FeynmanConcept(BaseModel):
    """Gradeable Feynman payload for one load-bearing concept in a source.

    The handle is a short reusable noun phrase that becomes the bower's title.
    The test_question + model_answer pair is the quiz scaffold for `peck`.
    """

    model_config = ConfigDict(extra="forbid")

    handle: str = Field(
        description="2-5 word noun phrase — the reusable concept title. NOT a "
        "sentence or claim (e.g. 'Retrieval-augmented generation', not 'RAG is "
        "useful'). Must match a topic in the outer topics list."
    )
    definition: str = Field(
        description="One plain sentence: what this concept IS. No jargon, no "
        "hedging — state it flatly."
    )
    why: str = Field(
        description="One line: why this concept matters — the practical payoff or "
        "insight the reader gains."
    )
    test_question: str = Field(
        description="A question that proves understanding of this concept. "
        "Answering it correctly requires genuine grasp, not recall."
    )
    model_answer: str = Field(
        description="A model answer written at the level of a thoughtful "
        "12-year-old — clear, concrete, no jargon. This is the grading target "
        "for `peck` (the quiz loop)."
    )


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
    """Structured output for the learned lane. Pointers, never a summary."""

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
    topics: list[str] = Field(
        description="The coarse topic notes this source feeds — a rich source "
        "usually feeds SEVERAL. Each is a SHORT, REUSABLE concept handle: a 2-5 "
        "word noun phrase many sources could link to (e.g. 'Agentic loops', "
        "'Verification in agent loops'), NOT a sentence/claim, NOT the source "
        "title. PREFER the supplied existing candidates; add a new handle only "
        "when no candidate fits and the topic is broad enough to reuse. [] if "
        "none fit."
    )
    connection: str = Field(
        description="One line on how these topics connect through this source — "
        "the pointer, not a summary."
    )
    concepts: list[FeynmanConcept] = Field(
        default_factory=list,
        description="Feynman payload for the LOAD-BEARING concepts only — the "
        "1-3 ideas this source most clearly illuminates. Skip minor topics. "
        "Each handle MUST appear in the topics list. [] if highlights are thin "
        "or no concept is clear enough to quiz on.",
    )
    tools: list[EntityRef] = Field(
        default_factory=list,
        description="Tools named in this source — repos, libraries, plugins, "
        "products worth their own shelf node (e.g. 'roboflow/supervision'). "
        "Attach the repo/product URL from the supplied links. [] if none.",
    )
    people: list[EntityRef] = Field(
        default_factory=list,
        description="People named in this source worth their own node — "
        "authors, creators, researchers, figures. Attach their profile/author "
        "URL from the supplied links. [] if none.",
    )


def _client() -> anthropic.Anthropic:
    # Reads ANTHROPIC_API_KEY from the environment.
    return anthropic.Anthropic()


def _first_text(message) -> str:
    for block in message.content:
        if block.type == "text":
            return block.text
    return ""


def describe_link(meta: PageMeta, model: str) -> str:
    prompt = (
        "Identify what this web page is in ONE short line (under 15 words). "
        "State the type and topic (e.g. 'Blog post on Rust async internals', "
        "'GitHub repo for a Telegram bot framework'). Do NOT summarise the "
        "content — you are labelling it for a reading queue, working only from "
        "the metadata below.\n\n"
        f"URL: {meta.url}\n"
        f"Title: {meta.title}\n"
        f"Description: {meta.description or '(none)'}"
    )
    msg = _client().messages.create(
        model=model,
        max_tokens=_DESCRIBE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    line = _first_text(msg).strip().strip('"')
    return line or meta.title


def synthesize_clipping(
    meta: PageMeta,
    note: str,
    candidate_links: list[str],
    model: str,
    highlights: list[str] | None = None,
    body_urls: list[str] | None = None,
) -> ClippingPlan:
    candidates = "\n".join(f"- {c}" for c in candidate_links) or "(none yet)"
    highlights = highlights or []
    body_urls = body_urls or []
    highlight_block = (
        "\nThe reader HIGHLIGHTED these passages — this is the signal for what "
        "mattered to them. Anchor your proposed concept and backlinks on these, "
        "not on the article as a whole:\n"
        + "\n".join(f"- {h}" for h in highlights)
        + "\n"
        if highlights
        else ""
    )
    links_block = (
        "\nLinks found in the note (use these as provenance URLs when you "
        "extract a tool or person — never invent a URL):\n"
        + "\n".join(f"- {u}" for u in body_urls)
        + "\n"
        if body_urls
        else ""
    )
    prompt = (
        "I have READ this source and want to file it into my evergreen knowledge "
        "vault. The vault links ideas with [[wikilinks]] and tags; folders don't "
        "matter.\n\n"
        "List the coarse topics this source feeds — a rich source usually feeds "
        "SEVERAL (don't force it down to one). PREFER the existing evergreen "
        "notes listed as candidates; add a new topic only when none fits and "
        "it's broad enough to reuse. Each topic is a SHORT, REUSABLE concept "
        "handle (a 2-5 word noun phrase many sources could link to, e.g. "
        "'Agentic loops'), NOT a sentence or claim, NOT the source title. Give "
        "pointers (what connects to what and why), never a summary.\n\n"
        "Also provide a Feynman payload for the 1-3 LOAD-BEARING concepts only "
        "(the ideas this source most sharply illuminates — skip minor ones). "
        "Each concept needs: handle (must match a topic above), a 1-line plain "
        "definition, a 1-line why-it-matters, a test question that requires real "
        "grasp, and a model answer at the level of a thoughtful 12-year-old. "
        "Skip concepts where the source is too thin to support a graded answer.\n\n"
        "Also extract named ENTITIES that deserve their own node: TOOLS (repos, "
        "libraries, plugins, products — e.g. 'roboflow/supervision') and PEOPLE "
        "(authors, creators, researchers, figures). Give each its provenance URL "
        "from the supplied links, a one-line note, and the topics it connects "
        "to. Extract only genuinely named things — [] if none.\n\n"
        f"Source URL: {meta.url}\n"
        f"Source title: {meta.title}\n"
        f"My note (why it matters): {note or '(none)'}\n"
        f"{highlight_block}"
        f"{links_block}\n"
        f"Existing evergreen notes (candidates for backlinks):\n{candidates}\n\n"
        f"Source excerpt (context only):\n{meta.body_excerpt[:3000]}"
    )
    response = _client().messages.parse(
        model=model,
        max_tokens=_CLIPPING_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_format=ClippingPlan,
    )
    plan = response.parsed_output
    if plan is None:
        # e.g. a refusal — fall back to a minimal, honest clipping.
        return ClippingPlan(
            concise_title=meta.title,
            description=meta.title,
            topics=[],
            connection="",
            concepts=[],
        )
    return plan
