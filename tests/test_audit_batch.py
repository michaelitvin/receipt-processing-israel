import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from shared.excel_generator import ExcelGenerator
from audit_batch import parse_batch


def build_receipt(name, number, date, total, line_items, vendor="ספק בדיקה", vendor_id="510"):
    return {
        "status": "success",
        "receipt_info": {"number": number, "vendor": vendor, "vendor_id": vendor_id,
                         "date": date, "document_type": "invoice+receipt",
                         "original_file": name, "reasoning": "בדיקה", "currency": "ILS"},
        "amounts": {"total_excl_vat": round(total / 1.18, 2),
                    "vat_amount": round(total - total / 1.18, 2),
                    "total_incl_vat": total},
        "classification": {"category": "דלק", "confidence": 0.9},
        "line_items": line_items,
    }


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("batch")
    images = tmp / "images"
    images.mkdir()
    for stem in ("one", "two"):
        Image.new("RGB", (800, 1200), "white").save(images / f"{stem}.jpg")
    receipts = [
        build_receipt("one.pdf", "111", "2026-05-10", 118.0,
                      [{"description": "פריט", "amount_excl_vat": 100.0, "vat": 18.0,
                        "total": 118.0, "deductible": True}]),
        build_receipt("two.pdf", "", "2022-01-01", 0,
                      [{"description": "ריק", "amount_excl_vat": 0, "vat": 0,
                        "total": 0, "deductible": True}]),
    ]
    gen = ExcelGenerator(REPO / "docs" / "extraction-prompt" / "001-ICOUNT_CATEGORIES.md")
    wb = gen.create_batch_workbook(receipts, images)
    xlsx = tmp / "batch.xlsx"
    wb.save(xlsx)
    return xlsx


def run_cli(*args):
    result = subprocess.run(
        ["uv", "run", "python", str(REPO / "tools" / "audit_batch.py"), *map(str, args)],
        capture_output=True, cwd=REPO)
    assert result.returncode in (0, 1), result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout.decode("utf-8")), result.returncode


def test_parse_batch_round_trips_values(batch):
    receipts = parse_batch(batch)
    assert len(receipts) == 2
    r1 = receipts[0]
    assert r1["sheet"] == "R001"
    assert r1["receipt_info"]["number"] == "111"
    assert r1["receipt_info"]["vendor"] == "ספק בדיקה"
    assert float(r1["amounts"]["total_incl_vat"]) == 118.0
    assert r1["line_items"][0]["description"] == "פריט"
    assert r1["line_items"][0]["deductible"] is True
    assert r1["image_jpg"].endswith("one.jpg")
    # the generator's sum row must NOT be parsed as a line item
    assert all(li["description"] != 'סה"כ פריטים' for li in r1["line_items"])


def test_manifest_cli(batch):
    manifest, _ = run_cli("manifest", batch)
    assert manifest["R001"]["receipt_info"]["number"] == "111"
    assert manifest["R002"]["amounts"]["total_incl_vat"] in (0, 0.0, "0")


def test_check_cli_flags_bad_sheet_only(batch):
    issues, rc = run_cli("check", batch, "--period", "2026-05")
    assert "R001" not in issues
    r2 = issues["R002"]
    assert any("חסר מספר קבלה" in w for w in r2)
    assert any("מחוץ לתקופת הדיווח" in w for w in r2)
    assert rc == 1  # issues found -> exit 1


def test_agent_prompts_values_from_manifest(batch):
    prompts, _ = run_cli("agent-prompts", batch, "--chunk", "1")
    assert len(prompts) == 2  # 2 receipts, chunk size 1
    p1 = prompts[0]["prompt"]
    assert "111" in p1 and "118.0" in p1 and "ספק בדיקה" in p1
    assert "one.jpg" in p1
    # anti-anchoring: transcription instruction precedes the extracted values
    assert p1.index("TRANSCRIBE") < p1.index("extracted values for comparison")
    assert prompts[0]["label"] == "audit:R001"


def test_agent_prompts_chunking(batch):
    prompts, _ = run_cli("agent-prompts", batch, "--chunk", "6")
    assert len(prompts) == 1
    assert prompts[0]["label"] == "audit:R001-R002"
