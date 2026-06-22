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

from .fetch import PageMeta

# Haiku 4.5 does not take `thinking`/`effort` params — omit them. Small caps:
# these are one-liners and short JSON, not essays.
_DESCRIBE_MAX_TOKENS = 120
_CLIPPING_MAX_TOKENS = 700


class ClippingPlan(BaseModel):
    """Structured output for the learned lane. Pointers, never a summary."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        description="One factual line: what this source is (type + topic)."
    )
    proposed_backlinks: list[str] = Field(
        description="Existing evergreen-note titles this connects to. "
        "Prefer the supplied candidates; [] if none fit."
    )
    proposed_note_title: str = Field(
        description="A title for a NEW evergreen note this could seed, phrased "
        "as the idea (not the source). Empty string if not worth one."
    )
    connection: str = Field(
        description="One line on why these links connect — the pointer, not a summary."
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
) -> ClippingPlan:
    candidates = "\n".join(f"- {c}" for c in candidate_links) or "(none yet)"
    prompt = (
        "I have READ this source and written a one-line note on why it matters. "
        "Help me file it into my evergreen knowledge vault. The vault links "
        "ideas with [[wikilinks]] and tags; folders don't matter.\n\n"
        "Propose backlinks PREFERRING the existing evergreen notes listed as "
        "candidates; only suggest a brand-new note title when nothing fits. "
        "Give pointers (what connects to what and why), never a summary of the "
        "article.\n\n"
        f"Source URL: {meta.url}\n"
        f"Source title: {meta.title}\n"
        f"My note (why it matters): {note or '(none)'}\n\n"
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
            description=meta.title,
            proposed_backlinks=[],
            proposed_note_title="",
            connection="",
        )
    return plan
