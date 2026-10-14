"""Anthropic calls. First-party API, Haiku by default.

Two jobs, both small and cheap:

- `describe_link`  — one-line "what is it" metadata for the tools lane.
  Grounded ONLY on page metadata (title/description); never the body. This is
  identification, not a summary.
- `synthesize_clipping` — every shelve (antilibrary: everything sent is
  shelved immediately, no read gate) turns a source + optional seed thought
  into claim-shaped key ideas and a set of *proposed* [[backlinks]] drawn from
  existing evergreen notes.

The clipping contract is a pydantic model in `schema.py`; `messages.parse`
derives the JSON schema from it and validates the response back into the
model.
"""

import anthropic
from anthropic import omit

from bower_bird.config import LLMSettings
from bower_bird.schema import (
    CardDraft,
    CardPlan,
    ClippingPlan,
    JudgeVerdict,
    PageMeta,
    QuizQuestion,
)


def key_ideas_block(key_ideas: list[str], seed: str) -> str:
    ideas = "\n".join(f"- {idea}" for idea in key_ideas) if key_ideas else "(none)"
    seed_line = f"\nReader's own note: {seed}" if seed else ""
    return f"Key ideas:\n{ideas}{seed_line}"


def depth_instruction(box: int, linked_titles: list[str]) -> str:
    """Depth scales with Leitner box (#18): 0-1 recall, 2-3 explain-simply,
    4-5 application/connection (referencing linked nodes when available)."""
    if box <= 1:
        return "Ask a plain recall question about one of the key ideas."
    if box <= 3:
        return (
            "Ask an explain-it-simply (Feynman-style) question — the learner "
            "must restate a key idea in their own plain words, not just recall it."
        )
    if linked_titles:
        titles = ", ".join(f"[[{t}]]" for t in linked_titles)
        return (
            "Ask an application/connection question — how does a key idea here "
            f"relate to or apply with one of these linked notes: {titles}?"
        )
    return (
        "Ask an application question — how would you use or connect one of "
        "these key ideas elsewhere?"
    )


def generate_question(
    key_ideas: list[str],
    seed: str,
    linked_titles: list[str],
    box: int,
    llm: LLMSettings,
) -> QuizQuestion:
    """Generate a fresh, box-appropriate quiz question (Haiku, no stored payload).

    Grounded ONLY on the node's distilled key ideas + reader seed thought (+
    linked titles at application depth) — never the full source body.
    """
    prompt = (
        "You are writing ONE spaced-repetition quiz question for a learner "
        "reviewing a note in their knowledge vault. Ground the question ONLY "
        "in the material below — never invent facts from outside it.\n\n"
        f"{depth_instruction(box, linked_titles)}\n\n"
        f"{key_ideas_block(key_ideas, seed)}"
    )
    response = anthropic_client().messages.parse(
        model=llm.model,
        max_tokens=llm.question_max_tokens,
        output_config={"effort": llm.effort},
        messages=[{"role": "user", "content": prompt}],
        output_format=QuizQuestion,
    )
    result = response.parsed_output
    if result is None:  # e.g. a refusal — caller falls back to a self-quiz.
        raise RuntimeError("question generator returned nothing")
    return result


def judge_answer(
    question: str,
    key_ideas: list[str],
    seed: str,
    user_answer: str,
    llm: LLMSettings,
    linked_titles: list[str] | None = None,
) -> JudgeVerdict:
    """Grade a peck answer against the node's key ideas. LLM-as-judge (Haiku).

    Ground truth is the distilled key ideas (+ reader seed thought), not the
    full node body (#18). At application depth (box 4-5, when linked_titles is
    given) connection answers referencing a linked title get credit too.
    """
    linked_block = ""
    if linked_titles:
        titles = ", ".join(f"[[{t}]]" for t in linked_titles)
        linked_block = (
            f"\nLinked notes (credit answers that connect to these): {titles}"
        )
    prompt = (
        "You are grading a spaced-repetition recall answer, Feynman-style. Grade "
        "the learner's answer against the key ideas below (the ground truth):\n"
        "- strong: captures the load-bearing idea, essentially correct.\n"
        "- weak: partially right but vague on or missing the core point.\n"
        "- wrong: incorrect, or a non-answer (blank / 'I don't know').\n"
        "Grade the UNDERSTANDING, not the wording or length. Give a one/two-line "
        "rationale addressed to the learner.\n\n"
        f"Question: {question}\n"
        f"{key_ideas_block(key_ideas, seed)}{linked_block}\n"
        f"Learner's answer: {user_answer or '(blank)'}"
    )
    response = anthropic_client().messages.parse(
        model=llm.model,
        max_tokens=llm.judge_max_tokens,
        output_config={"effort": llm.effort},
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


CARD_RULES = (
    "- The question is shown with no other context, so name its subject. Never "
    "say 'the article', 'the author' or 'this source'.\n"
    "- Ask for one thing. Prefer why / how / what-happens-when over yes/no and "
    "over 'list the N things'.\n"
    "- The answer is at most 30 words and drawn ONLY from the claim — never "
    "add a fact from outside it. The full claim is shown under your answer, so "
    "do not restate all of it."
)


def write_cards(title: str, claims: list[str], llm: LLMSettings) -> CardPlan | None:
    """Write one Anki card per claim for a single source (Haiku).

    Grounded ONLY on the claims — the distilled key ideas already on the node.
    Returns None on a refusal; the caller must check the plan holds exactly
    one card per claim before using it.
    """
    numbered = "\n".join(f"{i}. {claim}" for i, claim in enumerate(claims, 1))
    prompt = (
        "You are writing spaced-repetition flashcards for a learner's own "
        "knowledge vault. Write exactly ONE card per claim below, in the same "
        "order.\n\n"
        f"{CARD_RULES}\n\n"
        f"Source title (context only): {title}\n\n"
        f"Claims:\n{numbered}"
    )
    response = anthropic_client().messages.parse(
        model=llm.model,
        max_tokens=llm.card_max_tokens,
        output_config={"effort": llm.effort},
        messages=[{"role": "user", "content": prompt}],
        output_format=CardPlan,
    )
    return response.parsed_output


def rewrite_card(
    claim: str, front: str, back: str, problem: str, llm: LLMSettings
) -> CardDraft | None:
    """Replace a card that review showed is not working (Haiku).

    `problem` is one sentence saying what went wrong and what to change. The
    claim stays the ground truth; only the question and short answer move.
    Returns None on a refusal.
    """
    prompt = (
        "You are fixing a spaced-repetition flashcard in a learner's own "
        f"knowledge vault. {problem}\n\n"
        f"{CARD_RULES}\n\n"
        f"Claim (ground truth): {claim}\n"
        f"Current question: {front}\n"
        f"Current answer: {back}"
    )
    response = anthropic_client().messages.parse(
        model=llm.model,
        max_tokens=llm.question_max_tokens,
        output_config={"effort": llm.effort},
        messages=[{"role": "user", "content": prompt}],
        output_format=CardDraft,
    )
    return response.parsed_output


def anthropic_client() -> anthropic.Anthropic:
    # Reads ANTHROPIC_API_KEY from the environment.
    return anthropic.Anthropic()


def first_text(message) -> str:
    for block in message.content:
        if block.type == "text":
            return block.text
    return ""


def describe_link(meta: PageMeta, llm: LLMSettings) -> str:
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
    msg = anthropic_client().messages.create(
        model=llm.model,
        max_tokens=llm.describe_max_tokens,
        output_config={"effort": llm.effort},
        messages=[{"role": "user", "content": prompt}],
    )
    line = first_text(msg).strip().strip('"')
    return line or meta.title


def synthesize_clipping(
    meta: PageMeta,
    note: str,
    candidate_index: str,
    model: str,
    llm: LLMSettings,
    highlights: list[str] | None = None,
    body_urls: list[str] | None = None,
    person_anchors: list[str] | None = None,
) -> ClippingPlan:
    # candidate_index is a relevance-ranked subset of `_index.md`, most-
    # relevant-to-this-source first, each line `- [[title]] · category ·
    # one-liner` (one-liner may be empty). It is the single, compact
    # link-candidate + category source (no per-page reads).
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
        "I'm shelving this source into my evergreen antilibrary vault — every "
        "capture is filed the moment it arrives, there's no 'read' status to "
        "earn first. The vault links ideas with [[wikilinks]] and tags; folders "
        "don't matter.\n\n"
        "List the coarse topics this source feeds — a rich source usually feeds "
        "SEVERAL (don't force it down to one). PREFER titles already in the index "
        "below; add a new topic only when none fits and it's broad enough to "
        "reuse. A precise new topic beats a stretched existing one — don't force "
        "this source onto a listed title whose meaning doesn't actually cover it. "
        "The candidates below are ordered most-relevant-to-this-source first. "
        "Each topic is a SHORT, REUSABLE concept handle (a 2-5 word noun "
        "phrase many sources could link to, e.g. 'Agentic loops'), NOT a sentence "
        "or claim, NOT the source title.\n\n"
        "Assign a single top-level CATEGORY (a short lowercase noun). REUSE a "
        "category already present in the index below when one fits; only coin a "
        "new one when none do — keep the category vocabulary small.\n\n"
        "Then distill the source's KEY IDEAS — 3-6 standalone, testable CLAIMS a "
        "reader could be quizzed on (e.g. 'self-attention replaces recurrence, "
        "buying parallelism', never a topic label like 'discusses attention'). "
        "Weight your signal: clipper ==highlights== (what the reader flagged) "
        "outrank a Telegram seed thought, which outranks the body read on its "
        "own terms. Draw claims ONLY from the body/highlights/seed thought below "
        "— never invented from your own prior knowledge of the topic. Scale to "
        "the source: a rich essay yields several claims; a single-claim tweet "
        "yields one thin (but still real) claim — thin is fine, it's not your "
        "job to pad it. A link-list source may yield few or none — its outbound "
        "links are the substance, not its prose. A thread or quote-tweet is one "
        "node with attribution preserved, not several. Give [] if the source is "
        "too thin to extract a real claim.\n\n"
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
        f"My seed thought (if any): {note or '(none)'}\n"
        f"{highlight_block}"
        f"{person_block}"
        f"{links_block}\n"
        f"Graph index (existing pages — link candidates + their categories):\n"
        f"{candidates}\n\n"
        f"Source excerpt (context only):\n{meta.body_excerpt[:3000]}"
    )
    # Only the Haiku lane takes llm.effort; the PDF lane's Sonnet keeps its default.
    effort = {"effort": llm.effort} if model == llm.model else omit
    response = anthropic_client().messages.parse(
        model=model,
        max_tokens=llm.clipping_max_tokens,
        output_config=effort,
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
