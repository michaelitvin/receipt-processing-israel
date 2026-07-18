import sys
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from shared.excel_generator import ExcelGenerator
from shared.excel_config import get_excel_config
from receipt_consolidator import ReceiptConsolidator


def build_receipt(name, number, date, total, line_items,
                  vendor="ספק בדיקה", vendor_id="510", category="סלולר"):
    return {
        "status": "success",
        "receipt_info": {"number": number, "vendor": vendor, "vendor_id": vendor_id,
                         "date": date, "document_type": "invoice",
                         "original_file": name, "reasoning": "בדיקה", "currency": "ILS"},
        "amounts": {"total_excl_vat": round(total / 1.18, 2),
                    "vat_amount": round(total - total / 1.18, 2),
                    "total_incl_vat": total},
        "classification": {"category": category, "confidence": 0.9},
        "line_items": line_items,
    }


def _li(desc, total, deductible):
    excl = round(total / 1.18, 2)
    return {"description": desc, "amount_excl_vat": excl,
            "vat": round(total - excl, 2), "total": total, "deductible": deductible}


def _make_batch(tmp_path, receipts, *, simulate_excel_sums=True):
    """Generate a batch workbook the way the extractor does.

    When ``simulate_excel_sums`` is set, the generator's ``=SUM(...)`` totals row
    (which openpyxl leaves without a cached value) is overwritten with a literal
    number, reproducing what Excel stores after a human opens the file. Without
    this the sum-row-pickup bug hides, because data_only reads the formula as None.
    """
    images = tmp_path / "images"
    images.mkdir(exist_ok=True)
    for r in receipts:
        stem = Path(r["receipt_info"]["original_file"]).stem
        Image.new("RGB", (800, 1200), "white").save(images / f"{stem}.jpg")

    gen = ExcelGenerator(REPO / "docs" / "extraction-prompt" / "001-icount-categories.md")
    wb = gen.create_batch_workbook(receipts, images)

    if simulate_excel_sums:
        cfg = get_excel_config()
        total_col = cfg.get_line_item_column("total")
        for idx, r in enumerate(receipts, start=1):
            items = r["line_items"]
            if not items:
                continue
            sum_row = cfg.line_items_start_row + len(items)  # row just below the items
            ws = wb[f"R{idx:03d}"]
            ws.cell(row=sum_row, column=total_col,
                    value=sum(li["total"] for li in items))

    xlsx = tmp_path / "batch.xlsx"
    wb.save(xlsx)
    return xlsx


def _consolidate(tmp_path, xlsx):
    consolidator = ReceiptConsolidator(output_dir=tmp_path / "out")
    receipts = consolidator._extract_receipts_from_excel(xlsx)
    by_sheet = {r["worksheet"]: r for r in receipts}
    return consolidator, by_sheet


def test_mixed_deductibility_imports_deductible_portion(tmp_path):
    # One deductible line (40.00), one not - only the deductible portion imports.
    cellular = build_receipt(
        "cellular.pdf", "1001", "2026-05-01", 80.0,
        [_li("מנוי א (מוכר)", 40.0, True),
         _li("מנוי ב (לא מוכר)", 40.0, False)],
        vendor="אקמי סלולר")
    xlsx = _make_batch(tmp_path, [cellular])
    consolidator, by_sheet = _consolidate(tmp_path, xlsx)

    receipt = by_sheet["R001"]
    # The סה"כ פריטים totals row must never be parsed as a line item.
    assert len(receipt["line_items"]) == 2
    row = consolidator._create_icount_row(receipt, None)
    assert row["סכום"] == 40.0


def test_fully_deductible_multiline_imports_full_total(tmp_path):
    receipt_in = build_receipt(
        "water.pdf", "1002", "2026-06-01", 176.0,
        [_li("רכישה והפקת מים", 90.0, True),
         _li("טיהור שפכים", 50.0, True),
         _li("שירות לקוחות", 36.0, True)],
        vendor="מים לעיר", category="מים")
    xlsx = _make_batch(tmp_path, [receipt_in])
    consolidator, by_sheet = _consolidate(tmp_path, xlsx)

    receipt = by_sheet["R001"]
    assert len(receipt["line_items"]) == 3  # sum row excluded
    row = consolidator._create_icount_row(receipt, None)
    assert row["סכום"] == 176.0


def test_sum_row_not_parsed_as_line_item(tmp_path):
    # Guard directly against the regression: with a valued sum row present, the
    # single real item is the only line item parsed.
    receipt_in = build_receipt(
        "fuel.pdf", "999", "2026-05-01", 400.0,
        [_li("דלק 95", 400.0, True)], vendor="תחנת דלק", category="דלק")
    xlsx = _make_batch(tmp_path, [receipt_in])
    _, by_sheet = _consolidate(tmp_path, xlsx)
    assert [li["description"] for li in by_sheet["R001"]["line_items"]] == ["דלק 95"]
