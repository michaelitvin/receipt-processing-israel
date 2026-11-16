"""Tests for the raster-only-PDF gate (extraction_bitmap) in ImageHandler.

A portable fixture stands in for Weezmo receipts: PIL saves an image-only PDF, so
it has no text layer and one embedded raster - the exact shape the gate targets.
"""
import shutil

import pytest
from PIL import Image, ImageDraw

from shared.image_handler import ImageHandler

poppler = pytest.mark.skipif(
    not (shutil.which("pdfimages") and shutil.which("pdftotext")),
    reason="poppler (pdfimages/pdftotext) not on PATH",
)


def _image_only_pdf(path, size=(800, 1120)):
    """An image-only PDF (no text layer, one embedded raster) - like Weezmo."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    for i in range(0, size[1], 40):
        d.text((30, i), f"line {i} ABC 123.45", fill="black")
    img.save(path, "PDF", resolution=72)
    return path


@poppler
def test_gate_fires_on_image_only_pdf(tmp_path):
    pdf = _image_only_pdf(tmp_path / "weezmo_like.pdf")
    bitmap = ImageHandler.extraction_bitmap(pdf)
    assert bitmap is not None
    assert bitmap.width >= ImageHandler.MIN_EMBEDDED_DIM
    assert bitmap.height >= ImageHandler.MIN_EMBEDDED_DIM


@poppler
def test_gate_skips_when_text_layer_present(tmp_path, monkeypatch):
    # same PDF, but pretend it has a rich text layer -> keep the raw-PDF path
    pdf = _image_only_pdf(tmp_path / "invoice_like.pdf")
    monkeypatch.setattr(ImageHandler, "_pdf_text_char_count", classmethod(lambda cls, p: 900))
    assert ImageHandler.extraction_bitmap(pdf) is None


def test_gate_skips_non_pdf(tmp_path):
    jpg = tmp_path / "receipt.jpg"
    Image.new("RGB", (800, 1120), "white").save(jpg, "JPEG")
    assert ImageHandler.extraction_bitmap(jpg) is None


def test_gate_returns_none_when_poppler_missing(tmp_path, monkeypatch):
    pdf = tmp_path / "x.pdf"
    Image.new("RGB", (800, 1120), "white").save(pdf, "PDF")
    # unknowable text layer -> do not gate
    monkeypatch.setattr(ImageHandler, "_pdf_text_char_count", classmethod(lambda cls, p: -1))
    assert ImageHandler.extraction_bitmap(pdf) is None


@poppler
def test_largest_embedded_image_prefers_content_over_blank(tmp_path):
    # a content image plus a bigger-but-blank page: blank compresses tiny, content wins
    from PIL import Image as I
    content = I.new("RGB", (800, 1120), "white")
    ImageDraw.Draw(content).text((40, 40), "REAL RECEIPT TEXT " * 20, fill="black")
    blank = I.new("RGB", (900, 1200), "white")
    pdf = tmp_path / "layered.pdf"
    content.save(pdf, "PDF", save_all=True, append_images=[blank], resolution=72)
    got = ImageHandler._largest_embedded_image(pdf)
    assert got is not None
    # the content page (more ink -> larger PNG) should be chosen, not the blank one
    assert got.size == (800, 1120)


def _text_pdf(path, text="Invented test receipt line 12345 " * 6):
    """A minimal one-page PDF with a real text layer (Helvetica, ASCII text)."""
    stream = f"BT /F1 10 Tf 20 700 Td ({text}) Tj ET".encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))
    return path


# Google Drive receipts often carry Hebrew filenames. Windows poppler/xpdf builds
# can't open non-ASCII argv paths: pdftotext then reported an empty text layer,
# so a normal text PDF looked raster-only to the gate.
HEBREW_NAME = "קבלה_דלק_בדיקה.pdf"


@poppler
def test_gate_fires_on_hebrew_named_image_only_pdf(tmp_path):
    pdf = _image_only_pdf(tmp_path / HEBREW_NAME)
    bitmap = ImageHandler.extraction_bitmap(pdf)
    assert bitmap is not None
    assert bitmap.size == (800, 1120)


@poppler
def test_text_layer_counted_on_hebrew_named_pdf(tmp_path):
    ascii_pdf = _text_pdf(tmp_path / "text_layer.pdf")
    hebrew_pdf = _text_pdf(tmp_path / HEBREW_NAME)
    expected = ImageHandler._pdf_text_char_count(ascii_pdf)
    assert expected >= ImageHandler.SPARSE_TEXT_MAX_CHARS
    assert ImageHandler._pdf_text_char_count(hebrew_pdf) == expected
    assert ImageHandler.extraction_bitmap(hebrew_pdf) is None
