"""PDF lane tests — url detection, text extraction, the shelve routing (bare
PDF link → straight to brain), and the sonnet lane selection (the PDF lane
alone runs synthesize_clipping on `paper_model`, everything else on `model`).
No network: safe_get and the LLM calls are stubbed; extraction runs on a real
handcrafted PDF.

Run: uv run python tests/test_pdf.py
"""

import tempfile
from pathlib import Path

from bower_bird import app
from bower_bird import fetch as fetch_mod
from bower_bird.config import Config
from bower_bird.fetch import is_pdf_url
from bower_bird.schema import ClippingPlan
from bower_bird.state import State

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if not cond:
        _failures += 1
        print(f"FAIL: {msg}")


def _config(root: Path) -> Config:
    return Config(
        telegram_bot_token="x",
        allowed_chat_ids="1",
        vault_path=root,
        state_path=root / "state.json",
        fetch_timeout=15,
        queue_limit=100,
    )


def _tiny_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF containing `text`, with a valid xref."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        None,  # content stream, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects[3] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        pass


def test_is_pdf_url() -> None:
    cases = [
        ("https://example.com/paper.pdf", True),
        ("https://example.com/paper.PDF?dl=1", True),
        ("https://arxiv.org/pdf/2401.12345", True),
        ("https://arxiv.org/abs/2401.12345", False),
        ("https://example.com/pdf-tools", False),
        ("https://example.com/article", False),
    ]
    for url, want in cases:
        check(is_pdf_url(url) is want, f"is_pdf_url({url}) should be {want}")


def test_fetch_pdf_extracts_text() -> None:
    orig = fetch_mod.safe_get
    fetch_mod.safe_get = lambda url, timeout: _FakeResponse(_tiny_pdf("Hello bower"))
    try:
        meta = fetch_mod.fetch_pdf("https://example.com/paper.pdf", timeout=1)
    finally:
        fetch_mod.safe_get = orig
    check("Hello bower" in meta.body_excerpt, "extracted text lands in body_excerpt")
    check(meta.title == "Hello bower", "first text line becomes the title")


def test_bare_pdf_link_routes_to_brain() -> None:
    models_used: list[str] = []

    def run(body: str, url: str = "https://example.com/paper.pdf") -> str:
        orig = (app.fetch_pdf, app.fetch_rendered, app.synthesize_clipping)
        app.fetch_pdf = lambda url, timeout: fetch_mod.PageMeta(
            url=url, title="A Paper", description="", body_excerpt=body
        )
        app.fetch_rendered = lambda url, timeout: (
            fetch_mod.PageMeta(
                url=url, title="An Article", description="d", body_excerpt=body
            ),
            body,
        )

        def fake_synthesize(meta, note, candidates, model, llm, **kw):
            models_used.append(model)
            return ClippingPlan(
                concise_title="A Paper",
                description="a paper",
                topics=["Attention"],
                key_ideas=["attention is enough"],
            )

        app.synthesize_clipping = fake_synthesize
        try:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d) / "BowerBird"
                root.mkdir(parents=True)
                cfg = _config(root)
                state = State(path=root / "state.json")
                return app.handle_update(cfg, state, url)
        finally:
            app.fetch_pdf, app.fetch_rendered, app.synthesize_clipping = orig

    receipt = run("full extracted text")
    check(receipt.startswith("🧠"), f"bare PDF files to brain, got: {receipt!r}")
    check("[[Attention]]" in receipt, "receipt carries the linked concepts")
    check(
        models_used[-1] == Config.llm.paper_model,
        f"PDF lane synthesizes on paper_model (sonnet), got {models_used[-1]!r}",
    )

    receipt = run("")  # scanned/unfetchable → no text
    check(receipt.startswith("✂️"), f"textless PDF goes to clip queue: {receipt!r}")

    # A plain HTML shelve stays on the cheap Tier-1 model — sonnet is PDF-only.
    receipt = run("article body", url="https://example.com/article")
    check(receipt.startswith("🧠"), f"html link shelves to brain: {receipt!r}")
    check(
        models_used[-1] == Config.llm.model,
        f"non-PDF lane stays on the Tier-1 model, got {models_used[-1]!r}",
    )


def main() -> int:
    test_is_pdf_url()
    test_fetch_pdf_extracts_text()
    test_bare_pdf_link_routes_to_brain()
    if _failures:
        print(f"\n{_failures} failure(s).")
        return 1
    print("OK: pdf lane tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
