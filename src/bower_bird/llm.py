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

from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field

from bower_bird.fetch import PageMeta

# Haiku 4.5 does not take `thinking`/`effort` params — omit them. Small caps:
# these are one-liners and short JSON, not essays.
_DESCRIBE_MAX_TOKENS = 120
_CLIPPING_MAX_TOKENS = 1200
_JUDGE_MAX_TOKENS = 300


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
        description="The source's distilled load-bearing ideas — the substance a "
        "reader should retain (e.g. for Ogilvy on writing: 'Write the way you "
        "talk', 'Never write more than two pages', 'Use short words'). 3-6 "
        "concrete bullets drawn ONLY from the supplied body/highlights — never "
        "invented from prior knowledge of the topic. [] if the body is too thin "
        "to extract real ideas (never pad).",
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


class JudgeVerdict(BaseModel):
    """LLM-as-judge grade for one `peck` recall answer.

    Grades the learner's typed answer against the bower's model answer, so the
    quiz loop is a real eval loop, not self-assessment.
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


def judge_answer(
    question: str, model_answer: str, user_answer: str, model: str
) -> JudgeVerdict:
    """Grade a peck answer against the model answer. LLM-as-judge (Haiku)."""
    prompt = (
        "You are grading a spaced-repetition recall answer, Feynman-style. Grade "
        "the learner's answer against the model answer:\n"
        "- strong: captures the load-bearing idea, essentially correct.\n"
        "- weak: partially right but vague on or missing the core point.\n"
        "- wrong: incorrect, or a non-answer (blank / 'I don't know').\n"
        "Grade the UNDERSTANDING, not the wording or length. Give a one/two-line "
        "rationale addressed to the learner.\n\n"
        f"Question: {question}\n"
        f"Model answer: {model_answer}\n"
        f"Learner's answer: {user_answer or '(blank)'}"
    )
    response = _client().messages.parse(
        model=model,
        max_tokens=_JUDGE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_format=JudgeVerdict,
    )
    verdict = response.parsed_output
    if verdict is None:  # e.g. a refusal — grade weak (keeps the box), flag it.
        return JudgeVerdict(
            grade="weak",
            rationale="(judge returned nothing — graded weak; override if wrong.)",
        )
    return verdict


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
    candidate_index: str,
    model: str,
    highlights: list[str] | None = None,
    body_urls: list[str] | None = None,
    person_anchors: list[str] | None = None,
) -> ClippingPlan:
    # candidate_index is the raw `_index.md` catalog — one line per existing
    # brain/ page (`- [[title]] · category · one-liner`). It is the single,
    # compact link-candidate + category source (no per-page reads).
    candidates = candidate_index.strip() or "(none yet)"
    highlights = highlights or []
    body_urls = body_urls or []
    person_anchors = person_anchors or []
    person_block = (
        "\nThe reader TAGGED these people with #person — extract each as a person "
        "entity (resolve the full name from the mention + context, attach a "
        "profile URL if one is in the links above):\n"
        + "\n".join(f"- {a}" for a in person_anchors)
        + "\n"
        if person_anchors
        else "\nThe reader tagged NO people (#person) — return people: [].\n"
    )
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
        "SEVERAL (don't force it down to one). PREFER titles already in the index "
        "below; add a new topic only when none fits and it's broad enough to "
        "reuse. Each topic is a SHORT, REUSABLE concept handle (a 2-5 word noun "
        "phrase many sources could link to, e.g. 'Agentic loops'), NOT a sentence "
        "or claim, NOT the source title.\n\n"
        "Assign a single top-level CATEGORY (a short lowercase noun). REUSE a "
        "category already present in the index below when one fits; only coin a "
        "new one when none do — keep the category vocabulary small.\n\n"
        "Then distill the source's KEY IDEAS — the load-bearing substance a "
        "reader should retain (3-6 concrete bullets). Draw them ONLY from the "
        "body and highlights below; never invent ideas from your own prior "
        "knowledge of the topic, and give [] if the body is too thin to extract "
        "real ideas.\n\n"
        "Extract TOOLS named in this source that deserve their own node (repos, "
        "libraries, plugins, products — e.g. 'roboflow/supervision'); give each "
        "its provenance URL from the supplied links, a one-line note, and the "
        "topics it connects to. [] if none.\n\n"
        "For PEOPLE, extract ONLY those the reader tagged with #person (listed "
        "below) — not every author or name mentioned. Separately, identify the "
        "source's AUTHOR (byline) for the author field — this is attribution, not "
        "a person node. Leave author '' if none is evident.\n\n"
        f"Source URL: {meta.url}\n"
        f"Source title: {meta.title}\n"
        f"My note (why it matters): {note or '(none)'}\n"
        f"{highlight_block}"
        f"{person_block}"
        f"{links_block}\n"
        f"Graph index (existing pages — link candidates + their categories):\n"
        f"{candidates}\n\n"
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
            category="",
            topics=[],
            key_ideas=[],
        )
    return plan
