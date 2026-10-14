import hashlib
import html
import re
from collections import defaultdict
from urllib.parse import quote

import httpx

from bower_bird.config import Config
from bower_bird.ingest import assert_writable, today_iso
from bower_bird.llm import rewrite_card, write_cards
from bower_bird.recall import load_claims
from bower_bird.schema import AnkiCard, AnkiNote, Claim

# The deck and note type bb owns; every query is scoped to the note type.
DECK = "BowerBird"
MODEL = "BowerBird"
FIELDS = ["Front", "Back", "Claim", "Source", "Url", "Obsidian", "ClaimKey"]
FRONT_TEMPLATE = '<div class="front">{{Front}}</div>'
BACK_TEMPLATE = (
    "{{FrontSide}}<hr id=answer>"
    '<div class="back">{{Back}}</div>'
    '<div class="claim">{{Claim}}</div>'
    '<div class="source"><a href="{{Obsidian}}">{{Source}}</a>'
    '{{#Url}} · <a href="{{Url}}">original</a>{{/Url}}</div>'
)
CSS = (
    ".card { font-family: -apple-system, sans-serif; font-size: 20px; "
    "text-align: left; max-width: 34em; margin: 0 auto; padding: 1em; }\n"
    ".claim { margin-top: 1em; font-size: 0.8em; opacity: 0.75; }\n"
    ".source { margin-top: 1em; font-size: 0.7em; opacity: 0.6; }"
)

ROOT_TAG = "bb"
CONCEPT_TAG_PREFIX = "bb::concept::"
REWORKED_TAG = "bb::reworked"
RED_FLAG = 1
# Lapses before a card is rewritten once. Well under Anki's own leech
# threshold (8, which suspends), so the rewrite lands while the card is live.
LEECH_LAPSES = 3

FLAGGED_PROBLEM = (
    "The learner flagged this card as a bad card. Write a different question "
    "on the same claim: clearer, unambiguous, answerable without the source."
)
LEECH_PROBLEM = (
    "The learner has forgotten this card {lapses} times. Make it easier to "
    "retrieve: ask about a narrower part of the claim and put a concrete cue "
    "in the question."
)


def anki_request(config: Config, action: str, **params):
    """Call one AnkiConnect action and return its result.

    Raises ``httpx.ConnectError`` when Anki is closed or the add-on is missing,
    and ``RuntimeError`` when AnkiConnect reports an error for the action.
    """
    response = httpx.post(
        config.anki_url,
        json={"action": action, "version": 6, "params": params},
        timeout=60,
    )
    payload = response.json()
    if payload["error"]:
        raise RuntimeError(f"AnkiConnect {action}: {payload['error']}")
    return payload["result"]


def claim_key(text: str) -> str:
    """Stable card identity for a claim: a hash of its verbatim text.

    Key ideas are append-only and never rewritten, so the text is a durable
    key that survives a node rename or a concept merge.
    """
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def concept_tags(claim: Claim) -> set[str]:
    """Anki tags mirroring the claim's concept links (tags cannot hold spaces)."""
    return {CONCEPT_TAG_PREFIX + re.sub(r"\s+", "_", t) for t in claim.topics}


def source_claims(config: Config) -> dict[str, Claim]:
    """Every source key idea in the graph, keyed by claim key."""
    return {claim_key(c.text): c for c in load_claims(config) if c.kind == "source"}


def deck_notes(config: Config) -> dict[str, AnkiNote]:
    """Every note of the owned note type, keyed by claim key."""
    raw = anki_request(config, "notesInfo", query=f"note:{MODEL}")
    notes = [AnkiNote.model_validate(n) for n in raw]
    return {n.fields["ClaimKey"]: n for n in notes}


def ensure_deck(config: Config) -> None:
    """Create the owned deck and note type if Anki does not have them yet."""
    anki_request(config, "createDeck", deck=DECK)
    if MODEL not in anki_request(config, "modelNames"):
        anki_request(
            config,
            "createModel",
            modelName=MODEL,
            inOrderFields=FIELDS,
            cardTemplates=[
                {"Name": "Recall", "Front": FRONT_TEMPLATE, "Back": BACK_TEMPLATE}
            ],
            css=CSS,
        )


def note_payload(config: Config, key: str, claim: Claim, front: str, back: str) -> dict:
    """Build the AnkiConnect note for one claim.

    Text is escaped, since Anki stores field values as HTML.
    """
    vault_root = config.vault_path.parent
    node_path = config.sources_dir.relative_to(vault_root) / claim.node
    obsidian = (
        f"obsidian://open?vault={quote(vault_root.name)}"
        f"&file={quote(str(node_path), safe='')}"
    )
    return {
        "deckName": DECK,
        "modelName": MODEL,
        "fields": {
            "Front": html.escape(front),
            "Back": html.escape(back),
            "Claim": html.escape(claim.text),
            "Source": html.escape(claim.node),
            "Url": claim.url,
            "Obsidian": obsidian,
            "ClaimKey": key,
        },
        "tags": sorted({ROOT_TAG} | concept_tags(claim)),
        # Identity is the claim key, checked here; Anki's own duplicate check
        # is on the question text, which two claims may share.
        "options": {"allowDuplicate": True},
    }


def retag(config: Config, claims: dict[str, Claim], notes: dict[str, AnkiNote]) -> int:
    """Re-point concept tags at the graph's current links; return notes changed.

    Concepts are plastic (weave relinks, merges, splits), so the tags follow.
    Only ``bb::concept::`` tags move — the reader's own tags are left alone.
    """
    changed = 0
    for key, note in notes.items():
        if key not in claims:
            continue
        want = concept_tags(claims[key])
        have = {t for t in note.tags if t.startswith(CONCEPT_TAG_PREFIX)}
        if want == have:
            continue
        if have - want:
            anki_request(
                config, "removeTags", notes=[note.note_id], tags=" ".join(have - want)
            )
        if want - have:
            anki_request(
                config, "addTags", notes=[note.note_id], tags=" ".join(want - have)
            )
        changed += 1
    return changed


def push(config: Config, limit: int | None = None, dry_run: bool = False) -> int:
    """Graph -> deck: card every source claim that has no note yet.

    One Haiku call per source, one card per claim. Idempotent: a claim whose
    key already sits in the deck is skipped, so a rerun after a failure only
    does the remainder. ``limit`` caps the sources carded in this run.
    ``dry_run`` lists what would be carded and writes nothing.
    """
    if not dry_run:
        ensure_deck(config)
    claims = source_claims(config)
    notes = deck_notes(config)

    missing: dict[str, list[tuple[str, Claim]]] = defaultdict(list)
    for key, claim in claims.items():
        if key not in notes:
            missing[claim.node].append((key, claim))
    todo = list(missing.items())[:limit]

    if dry_run:
        for node, pairs in todo:
            print(f"{len(pairs):>3}  {node}")
        print(
            f"anki push (dry run): {sum(len(p) for _, p in todo)} card(s) "
            f"from {len(todo)} source(s) to write, {len(notes)} already in the deck."
        )
        return 0

    retagged = retag(config, claims, notes)
    added = 0
    for node, pairs in todo:
        plan = write_cards(node, [c.text for _, c in pairs], config.llm)
        if plan is None or len(plan.cards) != len(pairs):
            print(f"anki push: skipped [[{node}]] — card plan did not match claims.")
            continue
        anki_request(
            config,
            "addNotes",
            notes=[
                note_payload(config, key, claim, draft.front, draft.back)
                for (key, claim), draft in zip(pairs, plan.cards, strict=True)
            ],
        )
        added += len(pairs)
    if added or retagged:
        print(f"anki push: {added} card(s) added, {retagged} retagged.")
    return 0


def rework(
    config: Config,
    claims: dict[str, Claim],
    notes: dict[str, AnkiNote],
    cards: list[AnkiCard],
) -> int:
    """Rewrite cards that review showed are not working; return the count.

    Two triggers: the reader's red flag (rewrites every time it is set, then
    clears it), and ``LEECH_LAPSES`` lapses (rewrites once, marked with
    ``REWORKED_TAG``). A card that keeps lapsing after its rewrite is left
    alone — the claim itself is the problem and surfaces in the recall map.
    """
    by_id = {n.note_id: n for n in notes.values()}
    count = 0
    for card in cards:
        note = by_id[card.note]
        claim = claims.get(note.fields["ClaimKey"])
        flagged = card.flags == RED_FLAG
        leech = card.lapses >= LEECH_LAPSES and REWORKED_TAG not in note.tags
        if claim is None or not (flagged or leech):
            continue
        problem = (
            FLAGGED_PROBLEM if flagged else LEECH_PROBLEM.format(lapses=card.lapses)
        )
        draft = rewrite_card(
            claim.text,
            html.unescape(note.fields["Front"]),
            html.unescape(note.fields["Back"]),
            problem,
            config.llm,
        )
        if draft is None:
            continue
        anki_request(
            config,
            "updateNoteFields",
            note={
                "id": note.note_id,
                "fields": {
                    "Front": html.escape(draft.front),
                    "Back": html.escape(draft.back),
                },
            },
        )
        if flagged:
            anki_request(
                config,
                "setSpecificValueOfCard",
                card=card.card_id,
                keys=["flags"],
                newValues=[0],
            )
        if card.lapses >= LEECH_LAPSES:
            anki_request(config, "addTags", notes=[note.note_id], tags=REWORKED_TAG)
        count += 1
    return count


def render_recall_map(
    claims: dict[str, Claim], notes: dict[str, AnkiNote], cards: list[AnkiCard]
) -> str | None:
    """Roll card state up through the graph's concept links.

    Returns None when no card has been reviewed yet.

    Concepts come from the graph, not from Anki's tags, so the map follows the
    current links. Weakest first: most lapses, then shortest mean interval.
    """
    key_of = {n.note_id: key for key, n in notes.items()}
    by_concept: dict[str, list[AnkiCard]] = defaultdict(list)
    hard: list[tuple[AnkiCard, Claim]] = []
    for card in cards:
        claim = claims.get(key_of[card.note])
        if claim is None:
            continue
        for topic in claim.topics:
            by_concept[topic].append(card)
        if card.lapses >= LEECH_LAPSES:
            hard.append((card, claim))

    rows = []
    for concept, group in by_concept.items():
        seen = [c for c in group if c.reps]
        if not seen:
            continue
        lapses = sum(c.lapses for c in seen)
        interval = sum(max(c.interval, 0) for c in seen) / len(seen)
        rows.append((concept, len(group), len(seen), lapses, interval))
    if not rows:
        return None
    rows.sort(key=lambda r: (-r[3], r[4], r[0]))

    lines = [
        "# Recall map",
        "",
        f"Anki review state rolled up by concept, weakest first. Rebuilt by "
        f"`bb anki harvest` on {today_iso()}.",
        "",
        "| Concept | Cards | Seen | Lapses | Mean interval (days) |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| [[{concept}]] | {total} | {seen} | {lapses} | {interval:.0f} |"
        for concept, total, seen, lapses, interval in rows
    ]
    if hard:
        lines += [
            "",
            "## Hard claims",
            "",
            f"Forgotten {LEECH_LAPSES}+ times. The card was rewritten once; a "
            "claim still listed here needs a deeper key idea.",
            "",
        ]
        hard.sort(key=lambda pair: -pair[0].lapses)
        lines += [f"- ({c.lapses}) {claim.text} [[{claim.node}]]" for c, claim in hard]
    return "\n".join(lines) + "\n"


def harvest(config: Config) -> int:
    """Deck -> graph: rewrite failing cards, then rebuild the recall map."""
    claims = source_claims(config)
    notes = deck_notes(config)
    raw = anki_request(
        config, "cardsInfo", cards=[cid for n in notes.values() for cid in n.cards]
    )
    cards = [AnkiCard.model_validate(c) for c in raw]

    reworked = rework(config, claims, notes, cards)
    recall_map = render_recall_map(claims, notes, cards)
    if recall_map is not None:
        assert_writable(config, config.recall_map_path)
        config.recall_map_path.parent.mkdir(parents=True, exist_ok=True)
        config.recall_map_path.write_text(recall_map, encoding="utf-8")
    if reworked or recall_map is not None:
        print(
            f"anki harvest: {reworked} card(s) rewritten, "
            f"{sum(1 for c in cards if c.reps)}/{len(cards)} seen."
        )
    return 0


def main(config: Config, argv: list[str]) -> int:
    """`bb anki [push|harvest] [--limit N] [--dry-run]`. No verb runs both."""
    verb = argv[0] if argv and not argv[0].startswith("--") else None
    if verb not in (None, "push", "harvest"):
        print("bb anki: usage: bb anki [push|harvest] [--limit N] [--dry-run]")
        return 2
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    dry_run = "--dry-run" in argv
    try:
        if verb in (None, "push"):
            push(config, limit=limit, dry_run=dry_run)
        if verb in (None, "harvest") and not dry_run:
            harvest(config)
    except httpx.ConnectError:
        print(
            f"bb anki: no AnkiConnect at {config.anki_url}. Open Anki with the "
            "AnkiConnect add-on (code 2055492159) installed."
        )
        return 1
    return 0
