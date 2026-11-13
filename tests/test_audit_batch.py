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
    gen = ExcelGenerator(REPO / "docs" / "extraction-prompt" / "001-icount-categories.md")
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


def test_renamed_sheets_still_parse(batch, tmp_path):
    # A reviewed batch may carry vendor suffixes on sheet names (R001partner).
    from openpyxl import load_workbook
    work = tmp_path / "renamed.xlsx"
    import shutil as sh
    sh.copy2(batch, work)
    wb = load_workbook(work)
    wb["R001"].title = "R001partner"
    wb["R002"].title = "R002gas"
    wb.save(work)
    receipts = parse_batch(work)
    assert {r["sheet"] for r in receipts} == {"R001partner", "R002gas"}


def test_parse_batch_decodes_excel_resaved_link(batch, tmp_path):
    # Excel re-saves absolute links percent-encoded with backslashes. merge
    # derives the image filename from source_pdf, so an undecoded "%20" made
    # the image lookup miss for any filename with spaces.
    from openpyxl import load_workbook
    from shared.excel_config import get_excel_config
    import shutil as sh
    work = tmp_path / "resaved.xlsx"
    sh.copy2(batch, work)
    config = get_excel_config()
    fields = [f for _, f in config.get_header_fields()]
    link_row = config.header_start_row + fields.index("original_file")
    wb = load_workbook(work)
    wb["R001"].cell(row=link_row, column=config.header_value_column).hyperlink = (
        "file:///X:\\Some%20Folder\\a%20-%20b\\scan%20one.pdf")
    wb.save(work)
    r001 = next(r for r in parse_batch(work) if r["sheet"] == "R001")
    assert r001["source_pdf"] == "X:\\Some Folder\\a - b\\scan one.pdf"


def test_check_fails_loudly_when_no_receipt_sheets(batch, tmp_path):
    # Sheets that don't match R### must not be silently skipped to a green result.
    from openpyxl import load_workbook
    work = tmp_path / "nosheets.xlsx"
    import shutil as sh
    sh.copy2(batch, work)
    wb = load_workbook(work)
    wb["R001"].title = "junk1"
    wb["R002"].title = "junk2"
    wb.save(work)
    result = subprocess.run(
        ["uv", "run", "python", str(REPO / "tools" / "audit_batch.py"), "check", str(work)],
        capture_output=True, cwd=REPO)
    assert result.returncode == 2, result.stderr.decode("utf-8", "replace")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert "workbook" in payload


def test_agent_prompts_fails_loudly_when_no_receipt_sheets(batch, tmp_path):
    # agent-prompts must fail loudly on zero matching sheets, like check/manifest.
    from openpyxl import load_workbook
    work = tmp_path / "nosheets_prompts.xlsx"
    import shutil as sh
    sh.copy2(batch, work)
    wb = load_workbook(work)
    wb["R001"].title = "junk1"
    wb["R002"].title = "junk2"
    wb.save(work)
    result = subprocess.run(
        ["uv", "run", "python", str(REPO / "tools" / "audit_batch.py"),
         "agent-prompts", str(work), "--scratch", str(tmp_path)],
        capture_output=True, cwd=REPO)
    assert result.returncode == 2, result.stderr.decode("utf-8", "replace")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert "workbook" in payload


def test_recurring_reports_missing_vendor(batch, monkeypatch, capsys):
    import argparse
    import audit_batch
    monkeypatch.setattr(audit_batch, "_load_recurring_vendors", lambda: [
        {"name": "Present Co", "keywords": ["ספק בדיקה"]},
        {"name": "Missing Co", "keywords": ["nonexistent vendor"]},
    ])
    rc = audit_batch.cmd_recurring(argparse.Namespace(xlsx=[batch]))
    out = json.loads(capsys.readouterr().out)
    assert out["missing"] == ["Missing Co"]
    assert "Present Co" in out["present"]
    assert rc == 1


def test_recurring_no_config_is_noop(batch, monkeypatch, capsys):
    import argparse
    import audit_batch
    monkeypatch.setattr(audit_batch, "_load_recurring_vendors", lambda: [])
    rc = audit_batch.cmd_recurring(argparse.Namespace(xlsx=[batch]))
    assert rc == 0
    assert "note" in json.loads(capsys.readouterr().out)


def test_agent_prompts_values_from_manifest(batch):
    prompts, _ = run_cli("agent-prompts", batch, "--chunk", "1")
    assert len(prompts) == 2  # 2 receipts, chunk size 1
    p1 = prompts[0]["prompt"]
    assert "111" in p1 and "total=118" in p1 and "ספק בדיקה" in p1
    assert "one.jpg" in p1
    # anti-anchoring: transcription instruction precedes the extracted values
    assert p1.index("TRANSCRIBE") < p1.index("extracted values for comparison")
    assert prompts[0]["label"] == "audit:R001"


def test_agent_prompts_chunking(batch):
    prompts, _ = run_cli("agent-prompts", batch, "--chunk", "6")
    assert len(prompts) == 1
    assert prompts[0]["label"] == "audit:R001-R002"


def test_apply_fixes_and_verify(batch, tmp_path):
    import shutil as sh
    work = tmp_path / "work.xlsx"
    sh.copy2(batch, work)
    fixes = [
        {"sheet": "R001", "field": "vendor", "value": "ספק מתוקן", "note": "תוקן בביקורת"},
        {"sheet": "R002", "line_item": 0,
         "values": {"description": "דלק", "amount_excl_vat": 100.0, "vat": 18.0, "total": 118.0}},
        {"sheet": "R002", "field": "total_incl_vat", "value": 118.0},
        {"sheet": "R002", "field": "total_excl_vat", "value": 100.0},
        {"sheet": "R002", "field": "vat_amount", "value": 18.0},
        {"sheet": "R002", "non_expense": True, "note": "לא הוצאה"},
    ]
    fixes_path = tmp_path / "fixes.json"
    fixes_path.write_text(json.dumps(fixes, ensure_ascii=False), encoding="utf-8")
    backup_dir = tmp_path / "backups"

    out, rc = run_cli("apply-fixes", work, fixes_path, "--backup-dir", backup_dir)
    assert rc == 0
    assert out["applied"] == 6
    assert list(backup_dir.glob("*.xlsx")), "backup must exist"

    from openpyxl import load_workbook
    wb = load_workbook(work)
    from shared.excel_config import get_excel_config
    config = get_excel_config()
    fields = [f for _, f in config.get_header_fields()]
    vendor_row = config.header_start_row + fields.index("vendor")
    ws1 = wb["R001"]
    assert ws1.cell(row=vendor_row, column=config.header_value_column).value == "ספק מתוקן"
    note = ws1.cell(row=vendor_row, column=config.header_value_column + 2).value
    assert note == "תוקן בביקורת"
    ws2 = wb["R002"]
    assert "FF0000" in str(ws2.sheet_properties.tabColor.rgb)
    li_row = config.line_items_start_row
    assert ws2.cell(row=li_row, column=config.get_line_item_column("description")).value == "דלק"

    report, rc = run_cli("verify", work)
    assert rc == 0, report


def test_apply_fixes_resolved_clears_red_flag(batch, tmp_path):
    import shutil as sh
    from openpyxl import load_workbook
    from shared.excel_config import get_excel_config
    work = tmp_path / "work.xlsx"
    sh.copy2(batch, work)
    config = get_excel_config()
    notes_col = config.header_value_column + 2
    # simulate the generator's review warning on R001
    wb = load_workbook(work)
    ws = wb["R001"]
    ws.sheet_properties.tabColor = "FF0000"
    ws.cell(row=config.header_start_row, column=notes_col, value="לבדיקה ידנית: חסר תז/חפ הספק")
    wb.save(work)

    fixes = [
        {"sheet": "R001", "resolved": True, "note": "נבדק: ספק זר ללא ח.פ"},
        {"sheet": "R002", "resolved": True},
    ]
    fixes_path = tmp_path / "fixes.json"
    fixes_path.write_text(json.dumps(fixes, ensure_ascii=False), encoding="utf-8")
    out, rc = run_cli("apply-fixes", work, fixes_path, "--backup-dir", tmp_path / "b")
    assert rc == 0 and out["applied"] == 2

    wb = load_workbook(work)
    ws1 = wb["R001"]
    assert ws1.sheet_properties.tabColor is None
    assert ws1.cell(row=config.header_start_row, column=notes_col).value == "נבדק: ספק זר ללא ח.פ"
    report, rc = run_cli("verify", work)
    assert rc == 0, report


def test_apply_fixes_resolved_without_note_drops_only_the_warning(batch, tmp_path):
    import shutil as sh
    from openpyxl import load_workbook
    from shared.excel_config import get_excel_config
    work = tmp_path / "work.xlsx"
    sh.copy2(batch, work)
    config = get_excel_config()
    notes_col = config.header_value_column + 2
    wb = load_workbook(work)
    wb["R001"].sheet_properties.tabColor = "FF0000"
    wb["R001"].cell(row=config.header_start_row, column=notes_col, value="לבדיקה ידנית: x")
    wb["R002"].sheet_properties.tabColor = "FF0000"
    wb["R002"].cell(row=config.header_start_row, column=notes_col, value="ביקורת: הערה קיימת")
    wb.save(work)

    fixes = [{"sheet": "R001", "resolved": True}, {"sheet": "R002", "resolved": True}]
    fixes_path = tmp_path / "fixes.json"
    fixes_path.write_text(json.dumps(fixes, ensure_ascii=False), encoding="utf-8")
    run_cli("apply-fixes", work, fixes_path, "--backup-dir", tmp_path / "b")

    wb = load_workbook(work)
    assert wb["R001"].cell(row=config.header_start_row, column=notes_col).value is None
    # an audit note is not a review warning - keep it
    assert wb["R002"].cell(row=config.header_start_row, column=notes_col).value == "ביקורת: הערה קיימת"
    assert wb["R002"].sheet_properties.tabColor is None


def test_verify_catches_broken_arithmetic(batch, tmp_path):
    import shutil as sh
    work = tmp_path / "broken.xlsx"
    sh.copy2(batch, work)
    fixes = [{"sheet": "R001", "field": "total_incl_vat", "value": 999.0}]
    fixes_path = tmp_path / "fixes.json"
    fixes_path.write_text(json.dumps(fixes), encoding="utf-8")
    run_cli("apply-fixes", work, fixes_path, "--backup-dir", tmp_path / "b")
    report, rc = run_cli("verify", work)
    assert rc == 1
    assert any("R001" in k for k in report)


def _write_past_import(path, rows):
    """A minimal icount_import_*.xls in the consolidator's column order."""
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("x")
    header = ["תז/חפ הספק", "שם הספק", "שם סוג הוצאה", "סכום", "מטבע", "שער",
              "סוג מסמך", "מספר מסמך", "תאריך האסמכתא"]
    for c, h in enumerate(header):
        ws.write(0, c, h)
    for r, row in enumerate(rows, 1):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))


def test_history_flags_duplicate_drift_and_missing(batch, tmp_path, capsys):
    import argparse
    import audit_batch
    past = tmp_path / "archive" / "consolidation_1" / "icount_import_1.xls"
    _write_past_import(past, [
        # same number as R001, name written the way the consolidator exports it
        ["510", "ספק בדיקה בעמ", "דלק (ללא מע״מ)", "118.0", "", "", "invrec", "111", "2026-03-10"],
        # a vendor filed in the last period and before it, absent from the batch
        ["777", "Vanished Ltd", "אינטרנט", "50", "", "", "invoice", "9", "2026-03-11"],
        ["777", "Vanished Ltd", "אינטרנט", "50", "", "", "invoice", "10", "2026-01-11"],
    ])
    # a re-run of the same consolidation must not double-count
    _write_past_import(past.with_name("icount_import_2.xls"), [
        ["777", "Vanished Ltd", "אינטרנט", "50", "", "", "invoice", "9", "2026-03-11"],
    ])
    # this period's own consolidation (produced after the period ended) is not history
    _write_past_import(tmp_path / "archive" / "consolidation_9" / "icount_import_20260904_120000.xls", [
        ["510", "ספק בדיקה", "דלק", "118.0", "", "", "invrec", "111", "2026-05-10"],
    ])
    rc = audit_batch.cmd_history(argparse.Namespace(xlsx=[batch], past=[tmp_path / "archive"],
                                                    period="2026-03"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["past_rows"] == 3
    assert out["excluded_files"] == ["icount_import_20260904_120000.xls"]
    assert out["last_filed_period"] == "2026-03"
    dup = out["duplicates"]
    assert len(dup) == 1 and dup[0]["sheet"] == "R001" and dup[0]["number"] == "111"
    assert dup[0]["already_filed"]["file"] == "icount_import_1.xls"
    fields = {(i["sheet"], i["field"]) for i in out["inconsistent"]}
    assert ("R001", "vendor") in fields          # "ספק בדיקה" vs "ספק בדיקה בעמ" is drift
    assert ("R001", "category") not in fields    # "(ללא מע״מ)" suffix stripped before comparing
    assert [m["vendor"] for m in out["missing_vs_previous"]] == ["Vanished Ltd"]
    assert out["missing_vs_previous"][0]["in_last_filed_period"] is True
    assert out["missing_vs_previous"][0]["periods_seen"] == 2


def test_history_no_past_is_noop(batch, tmp_path, capsys):
    import argparse
    import audit_batch
    rc = audit_batch.cmd_history(argparse.Namespace(xlsx=[batch], past=[tmp_path], period=None))
    assert rc == 0
    assert "note" in json.loads(capsys.readouterr().out)


def test_merge_preserves_names_notes_and_drops(batch, tmp_path, capsys):
    import argparse
    import audit_batch
    from openpyxl import load_workbook
    # give R001 an audit note first, so we can see it survive the merge
    fixes = tmp_path / "fixes.json"
    fixes.write_text(json.dumps([{"sheet": "R001", "field": "vendor", "value": "ספק בדיקה",
                                  "note": "הערת ביקורת"}]), encoding="utf-8")
    src = tmp_path / "src.xlsx"
    import shutil
    shutil.copy2(batch, src)
    shutil.copytree(batch.parent / "images", tmp_path / "images")
    assert audit_batch.cmd_apply_fixes(argparse.Namespace(
        xlsx=src, fixes=fixes, backup_dir=tmp_path / "bk")) == 0
    capsys.readouterr()

    out = tmp_path / "merged" / "merged.xlsx"
    rc = audit_batch.cmd_merge(argparse.Namespace(out=out, xlsx=[src, src], drop=["R002"]))
    info = json.loads(capsys.readouterr().out)
    assert rc == 0
    # first source keeps its names; the second source's R001 collides -> next free
    # number AFTER the dropped R002, which is never reused
    assert info["mapping"] == {"0:src.xlsx": {"R001": "R001"}, "1:src.xlsx": {"R001": "R003"}}
    wb = load_workbook(out)
    assert [n for n in wb.sheetnames if n.startswith("R")] == ["R001", "R003"]
    assert info["dropped"] == ["0:src.xlsx:R002", "1:src.xlsx:R002"]
    assert wb["R001"]["D3"].value == "הערת ביקורת"           # audit note carried over
    assert wb["R003"]["D3"].value == "הערת ביקורת"
    assert len(wb["R001"]._images) == 1                       # image embedded via the generator
    parsed = {r["sheet"]: r for r in parse_batch(out)}
    assert parsed["R003"]["receipt_info"]["number"] == "111"
    assert parsed["R003"]["amounts"]["total_incl_vat"] == 118.0
    assert audit_batch.cmd_verify(argparse.Namespace(xlsx=out)) == 0
