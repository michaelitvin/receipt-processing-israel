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
