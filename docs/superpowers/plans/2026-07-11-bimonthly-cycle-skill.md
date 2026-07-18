# Bi-Monthly Cycle Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A project skill that runs the full bi-monthly receipt cycle (extract → audit → human review → consolidate → reflect) backed by deterministic audit tooling and a shared structural-checks module.

**Architecture:** Structural checks live in `shared/receipt_checks.py` (pure functions over receipt dicts) with two callers: the extractor at generation time and `tools/audit_batch.py` on parsed workbooks. The audit tool is a five-subcommand CLI with JSON in/out so Claude never retypes values. The skill (`.claude/skills/bimonthly-cycle/SKILL.md`) encodes the process; personal audit knowledge lives in untracked `AUDIT_KNOWLEDGE.personal.md`.

**Tech Stack:** Python 3.13, uv, openpyxl, pytest (new dev dependency), existing `shared/excel_config.py` for workbook layout.

## Global Constraints

- Windows console is cp1252: every command printing Hebrew must run with `PYTHONIOENCODING=utf-8`; `audit_batch.py` calls `sys.stdout.reconfigure(encoding='utf-8')` itself.
- Hebrew warning strings below are exact copy — reuse verbatim, do not rephrase.
- Workbook layout comes from `shared/excel_config.py` (`get_excel_config()`) — never hardcode rows/columns except through it.
- Never set `img.width`/`img.height` on images of a **loaded** workbook — resize via `anchor.ext` in EMU (px × 9525).
- Never commit `*.personal.md` files or real receipt data (both gitignored; verify with `git check-ignore`).
- Receipt dict shape everywhere: `{'receipt_info': {...}, 'amounts': {...}, 'classification': {...}, 'line_items': [...]}` with english keys per `excel_layout.yaml` field_mappings.
- `receipt_extractor.py` recently gained `estimate_cost_usd` import and cost handling — leave that code untouched.
- Run all commands from repo root `D:\code\receipt-processing-israel` via `uv run`.

---

### Task 1: `shared/receipt_checks.py` with tests

**Files:**
- Create: `shared/receipt_checks.py`
- Create: `tests/test_receipt_checks.py`
- Modify: `pyproject.toml` (pytest dev dependency, via `uv add --dev pytest`)

**Interfaces:**
- Produces: `parse_period(period: str) -> List[str]` — raises `ValueError` on bad input; returns two `'YYYY-MM'` prefixes.
- Produces: `check_receipt(receipt: Dict, period_months: Optional[List[str]] = None) -> List[str]` — warnings for one receipt.
- Produces: `check_batch(receipts: List[Dict], period_months: Optional[List[str]] = None) -> Dict[int, List[str]]` — per-index warnings incl. cross-receipt duplicates.

- [ ] **Step 1: Add pytest**

Run: `uv add --dev pytest`
Expected: `pyproject.toml` gains a dev dependency group with pytest.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_receipt_checks.py`:

```python
import pytest

from shared.receipt_checks import parse_period, check_receipt, check_batch


def make_receipt(number="123", vendor="ספק", vendor_id="5100", date="2026-05-10",
                 currency="ILS", net=100.0, vat=18.0, total=118.0, line_items=None):
    return {
        "receipt_info": {"number": number, "vendor": vendor, "vendor_id": vendor_id,
                         "date": date, "currency": currency},
        "amounts": {"total_excl_vat": net, "vat_amount": vat, "total_incl_vat": total},
        "line_items": line_items if line_items is not None else [],
    }


# ---- parse_period ----

def test_parse_period_maps_to_bimonthly_window():
    assert parse_period("2026-05") == ["2026-05", "2026-06"]
    assert parse_period("2026-06") == ["2026-05", "2026-06"]
    assert parse_period("2026-01") == ["2026-01", "2026-02"]
    assert parse_period("2026-12") == ["2026-11", "2026-12"]


@pytest.mark.parametrize("bad", ["2026-13", "garbage", "2026", "2026-00"])
def test_parse_period_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_period(bad)


# ---- check_receipt ----

def test_clean_receipt_has_no_warnings():
    assert check_receipt(make_receipt()) == []


def test_zero_total_missing_fields_flagged():
    r = make_receipt(number="", vendor_id="", date="", net=0, vat=0, total=0)
    warnings = check_receipt(r)
    assert 'סה"כ כולל מע"מ הוא 0 - ייתכן שהחילוץ נכשל' in warnings
    assert 'חסר מספר קבלה' in warnings
    assert 'חסר תז/חפ הספק' in warnings
    assert 'חסר תאריך' in warnings


def test_date_outside_period_flagged():
    r = make_receipt(date="2022-07-12")
    warnings = check_receipt(r, period_months=["2026-05", "2026-06"])
    assert any("2022-07-12" in w and "מחוץ לתקופת הדיווח" in w for w in warnings)
    assert check_receipt(r) == []  # no period configured -> not flagged


def test_arithmetic_mismatch_flagged():
    warnings = check_receipt(make_receipt(net=100, vat=18, total=200))
    assert any("אי-התאמה חשבונית" in w for w in warnings)


def test_one_agora_rounding_tolerated():
    # e.g. 200.00 + 36.00 = 236.00 printed as 235.99
    assert check_receipt(make_receipt(net=200.00, vat=36.00, total=235.99)) == []


def test_unusual_vat_rate_flagged():
    # 17% split of a 470 total
    warnings = check_receipt(make_receipt(net=401.71, vat=68.29, total=470))
    assert any('שיעור מע"מ חריג' in w for w in warnings)


def test_zero_vat_rate_not_flagged():
    assert check_receipt(make_receipt(net=118, vat=0, total=118)) == []


def test_unknown_currency_flagged():
    warnings = check_receipt(make_receipt(currency="GBP"))
    assert any("מטבע לא מוכר" in w for w in warnings)


def test_line_items_sum_mismatch_flagged():
    items = [{"total": 140.0}, {"total": 50.0}]  # 190 != 210
    warnings = check_receipt(make_receipt(net=177.97, vat=32.03, total=210, line_items=items))
    assert any("הפריטים מסתכמים" in w for w in warnings)


def test_line_items_sum_match_ok():
    items = [{"total": 40.0}, {"total": 40.0}]
    assert check_receipt(make_receipt(net=67.80, vat=12.20, total=80.0, line_items=items)) == []


# ---- check_batch ----

def test_duplicate_receipt_number_flagged_on_both():
    receipts = [make_receipt(number="777"), make_receipt(number="777", date="2026-05-11", total=118.0)]
    result = check_batch(receipts)
    assert any("כפילות אפשרית" in w for w in result[0])
    assert any("כפילות אפשרית" in w for w in result[1])


def test_same_vendor_total_date_flagged():
    receipts = [make_receipt(number="1"), make_receipt(number="2")]
    result = check_batch(receipts)
    assert any("כפילות אפשרית" in w for w in result[0])


def test_distinct_receipts_not_flagged():
    receipts = [make_receipt(number="1"), make_receipt(number="2", date="2026-06-07")]
    result = check_batch(receipts)
    assert result[0] == [] and result[1] == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_receipt_checks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.receipt_checks'`

- [ ] **Step 4: Implement `shared/receipt_checks.py`**

```python
# Copyright (c) 2025 Michael Litvin
# Licensed under AGPL-3.0-or-later - see LICENSE file for details
"""Structural sanity checks shared by the extractor and the audit tool.

Pure functions over plain receipt dicts shaped like extractor results:
    {'receipt_info': {...}, 'amounts': {...}, 'line_items': [...]}
Checks that need the source document (visual verification) do NOT belong
here - they are the audit's job, and their value is independence.
"""
from typing import Any, Dict, List, Optional

VALID_CURRENCIES = {'ILS', 'USD', 'EUR'}
AMOUNT_TOLERANCE = 0.02   # receipts themselves round by up to one agora
VAT_RATE = 0.18
VAT_RATE_TOLERANCE = 0.005


def parse_period(period: str) -> List[str]:
    """Parse YYYY-MM into the canonical bi-monthly VAT period containing it.

    Israeli VAT periods are Jan-Feb, Mar-Apr, May-Jun, Jul-Aug, Sep-Oct, Nov-Dec.
    Returns the two months as ['YYYY-MM', 'YYYY-MM'] prefixes for date matching.
    """
    try:
        year, month = map(int, period.split('-'))
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        raise ValueError(f"Invalid period {period!r}, expected YYYY-MM")
    start = month if month % 2 == 1 else month - 1
    return [f"{year:04d}-{start:02d}", f"{year:04d}-{start + 1:02d}"]


def check_receipt(receipt: Dict[str, Any],
                  period_months: Optional[List[str]] = None) -> List[str]:
    """Return warnings for a single receipt (empty list = clean)"""
    info = receipt.get('receipt_info', {})
    amounts = receipt.get('amounts', {})
    line_items = receipt.get('line_items', [])
    warnings: List[str] = []

    total = _num(amounts.get('total_incl_vat'))
    net = _num(amounts.get('total_excl_vat'))
    vat = _num(amounts.get('vat_amount'))

    if not total:
        warnings.append('סה"כ כולל מע"מ הוא 0 - ייתכן שהחילוץ נכשל')
    if not info.get('number'):
        warnings.append('חסר מספר קבלה')
    if not info.get('vendor_id'):
        warnings.append('חסר תז/חפ הספק')

    date = info.get('date') or ''
    if not date:
        warnings.append('חסר תאריך')
    elif period_months and str(date)[:7] not in period_months:
        warnings.append(
            f'תאריך {date} מחוץ לתקופת הדיווח ({period_months[0]} עד {period_months[1]})')

    currency = info.get('currency') or ''
    if currency and currency not in VALID_CURRENCIES:
        warnings.append(f'מטבע לא מוכר: {currency}')

    if total and abs(net + vat - total) > AMOUNT_TOLERANCE:
        warnings.append(f'אי-התאמה חשבונית: {net:g} + {vat:g} != {total:g}')

    if net > 0 and vat > 0:
        rate = vat / net
        if abs(rate - VAT_RATE) > VAT_RATE_TOLERANCE:
            warnings.append(f'שיעור מע"מ חריג: {rate:.1%}')

    if line_items:
        items_total = sum(_num(li.get('total')) for li in line_items)
        if total and abs(items_total - total) > AMOUNT_TOLERANCE:
            warnings.append(
                f'הפריטים מסתכמים ל-{items_total:g} במקום {total:g} - ייתכן פריט חסר')

    return warnings


def check_batch(receipts: List[Dict[str, Any]],
                period_months: Optional[List[str]] = None) -> Dict[int, List[str]]:
    """Per-receipt warnings plus cross-receipt duplicate detection"""
    result = {i: check_receipt(r, period_months) for i, r in enumerate(receipts)}

    by_number: Dict[tuple, List[int]] = {}
    by_signature: Dict[tuple, List[int]] = {}
    for i, r in enumerate(receipts):
        info = r.get('receipt_info', {})
        number = info.get('number')
        vendor = info.get('vendor')
        total = _num(r.get('amounts', {}).get('total_incl_vat'))
        if number:
            by_number.setdefault((str(number), vendor), []).append(i)
        if total:
            by_signature.setdefault((vendor, total, info.get('date')), []).append(i)

    for (number, vendor), idxs in by_number.items():
        if len(idxs) > 1:
            for i in idxs:
                result[i].append(
                    f'כפילות אפשרית: מספר קבלה {number} של {vendor} מופיע {len(idxs)} פעמים')
    for (vendor, total, date), idxs in by_signature.items():
        if len(idxs) > 1:
            for i in idxs:
                result[i].append(
                    f'כפילות אפשרית: {vendor} בסך {total:g} בתאריך {date} מופיע {len(idxs)} פעמים')
    return result


def _num(value: Any) -> float:
    """Lenient numeric coercion - xlsx round-trips may yield strings"""
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(',', ''))
    except ValueError:
        return 0.0
```

Also create empty `tests/__init__.py`? Not needed — pytest discovers without it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_receipt_checks.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add shared/receipt_checks.py tests/test_receipt_checks.py pyproject.toml uv.lock
git commit -m "feat: shared structural checks module for extractor and audit"
```

---

### Task 2: Wire extractor to shared checks

**Files:**
- Modify: `receipt_extractor.py` (`_parse_period`, `_add_review_warnings`, main() period validation)
- Create: `tests/test_extractor_warnings.py`

**Interfaces:**
- Consumes: `parse_period`, `check_batch` from Task 1.
- Produces: unchanged external behavior — successful results with problems gain `result['review_warnings']: List[str]`; `excel_generator._add_review_warnings` (existing) renders them.

- [ ] **Step 1: Write the failing test**

Create `tests/test_extractor_warnings.py`:

```python
from receipt_extractor import ReceiptExtractor


def make_result(status="success", number="9", vendor_id="1", date="2026-05-10", total=100.0):
    return {"status": status, "file_path": "x.pdf",
            "receipt_info": {"number": number, "vendor": "ספק", "vendor_id": vendor_id,
                             "date": date, "currency": "ILS"},
            "amounts": {"total_excl_vat": round(total / 1.18, 2),
                        "vat_amount": round(total - total / 1.18, 2),
                        "total_incl_vat": total},
            "line_items": []}


def make_extractor(period_months):
    ex = object.__new__(ReceiptExtractor)  # bypass __init__ (needs API key)
    ex.period_months = period_months
    return ex


def test_warnings_attached_only_to_bad_successful_results():
    ex = make_extractor(["2026-05", "2026-06"])
    results = [
        make_result(),                                    # clean
        make_result(number="", vendor_id="", total=0),    # bad
        make_result(date="2022-07-12"),                   # out of period
        {"status": "error", "file_path": "e.pdf"},        # error result untouched
    ]
    ex._add_review_warnings(results)
    assert "review_warnings" not in results[0]
    assert len(results[1]["review_warnings"]) >= 3
    assert any("2022-07-12" in w for w in results[2]["review_warnings"])
    assert "review_warnings" not in results[3]


def test_duplicates_detected_across_batch():
    ex = make_extractor(None)
    results = [make_result(number="777"), make_result(number="777")]
    ex._add_review_warnings(results)
    assert any("כפילות" in w for w in results[0]["review_warnings"])
    assert any("כפילות" in w for w in results[1]["review_warnings"])
```

- [ ] **Step 2: Run test — the duplicate test must FAIL**

Run: `uv run pytest tests/test_extractor_warnings.py -v`
Expected: `test_duplicates_detected_across_batch` FAILS (current implementation has no duplicate detection); the first test may pass already.

- [ ] **Step 3: Rewire `receipt_extractor.py`**

Add import near the other shared imports:

```python
from shared.receipt_checks import parse_period, check_batch
```

Delete the `_parse_period` staticmethod entirely. In `__init__` replace:

```python
        self.period_months = self._parse_period(period) if period else None
```
with:
```python
        self.period_months = parse_period(period) if period else None
```

Replace the whole `_add_review_warnings` method body with:

```python
    def _add_review_warnings(self, results: List[Dict[str, Any]]) -> None:
        """Attach review warnings to successful results with suspicious data"""
        successful = [r for r in results if r.get('status') == 'success']
        for idx, warnings in check_batch(successful, self.period_months).items():
            if warnings:
                successful[idx]['review_warnings'] = warnings
                logger.warning(
                    f"Review needed for {Path(successful[idx].get('file_path', '?')).name}: "
                    f"{'; '.join(warnings)}")
```

In `main()`, replace `ReceiptExtractor._parse_period(args.period)` with `parse_period(args.period)`.

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add receipt_extractor.py tests/test_extractor_warnings.py
git commit -m "refactor: extractor sanity checks delegate to shared receipt_checks"
```

---

### Task 3: `tools/audit_batch.py` — parse, manifest, check

**Files:**
- Create: `tools/audit_batch.py`
- Create: `tests/test_audit_batch.py`

**Interfaces:**
- Consumes: `check_batch`, `parse_period` (Task 1); `get_excel_config()` and `ExcelGenerator` (existing).
- Produces: `parse_batch(xlsx: Path) -> List[Dict]` — receipt dicts (standard shape) each with extra keys `sheet: str`, `source_pdf: str`, `image_jpg: str`. CLI: `manifest <xlsx>`, `check <xlsx> [--period YYYY-MM]`, both printing UTF-8 JSON to stdout.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit_batch.py` (fixture builds a real workbook via ExcelGenerator; reused by Tasks 4-5 tests):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit_batch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audit_batch'`

- [ ] **Step 3: Implement `tools/audit_batch.py` (manifest + check)**

```python
#!/usr/bin/env python3
# Copyright (c) 2025 Michael Litvin
# Licensed under AGPL-3.0-or-later - see LICENSE file for details
"""Deterministic audit tooling for extraction batch workbooks.

Subcommands (JSON in/out, UTF-8):
  manifest <xlsx>                       dump per-sheet receipt data
  check <xlsx> [--period YYYY-MM]      structural checks (exit 1 if issues)
  agent-prompts <xlsx> [--chunk N]     visual-verification agent prompts
  apply-fixes <xlsx> <fixes.json> --backup-dir DIR
  verify <xlsx>                        post-fix integrity (exit 1 if broken)

Used by the bimonthly-cycle skill; see .claude/skills/bimonthly-cycle/SKILL.md.
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font

from shared.excel_config import get_excel_config
from shared.receipt_checks import check_batch, parse_period

SHEET_RE = re.compile(r"R\d{3}")
LINE_ITEMS_SUM_LABEL = 'סה"כ פריטים'
AUDIT_NOTE_COLOR = "CC5500"

INFO_FIELDS = {"number", "vendor", "vendor_id", "date", "document_type",
               "currency", "original_file", "reasoning"}
AMOUNT_FIELDS = {"total_excl_vat", "vat_amount", "total_incl_vat"}


def parse_batch(xlsx: Path) -> list:
    """Parse an extraction batch workbook back into standard receipt dicts.

    Each dict gains extra keys: sheet, source_pdf, image_jpg.
    """
    config = get_excel_config()
    wb = load_workbook(xlsx)
    value_col = config.header_value_column
    receipts = []

    for name in wb.sheetnames:
        if not SHEET_RE.fullmatch(name):
            continue
        ws = wb[name]
        info, amounts, classification = {}, {}, {}
        source_pdf = ""

        row = config.header_start_row
        for _, field in config.get_header_fields():
            cell = ws.cell(row=row, column=value_col)
            value = cell.value
            if field in INFO_FIELDS:
                info[field] = "" if value is None else value
            elif field in AMOUNT_FIELDS:
                amounts[field] = 0 if value is None else value
            elif field == "category":
                classification["category"] = "" if value is None else value
            if field == "original_file" and cell.hyperlink:
                source_pdf = re.sub(r"^file:///", "", cell.hyperlink.target).replace("/", "\\")
            row += 1

        line_items = []
        li_row = config.line_items_start_row
        while li_row <= ws.max_row:
            desc = ws.cell(row=li_row, column=config.get_line_item_column("description")).value
            if desc is None or not str(desc).strip() or str(desc).strip() == LINE_ITEMS_SUM_LABEL:
                break
            line_items.append({
                "description": str(desc).strip(),
                "amount_excl_vat": ws.cell(row=li_row, column=config.get_line_item_column("amount_excl_vat")).value or 0,
                "vat": ws.cell(row=li_row, column=config.get_line_item_column("vat")).value or 0,
                "total": ws.cell(row=li_row, column=config.get_line_item_column("total")).value or 0,
                "deductible": bool(ws.cell(row=li_row, column=config.get_line_item_column("deductible")).value),
            })
            li_row += 1

        image_jpg = ""
        if info.get("original_file"):
            image_jpg = str(xlsx.parent / "images" / (Path(str(info["original_file"])).stem + ".jpg"))

        receipts.append({
            "sheet": name,
            "receipt_info": info,
            "amounts": amounts,
            "classification": classification,
            "line_items": line_items,
            "source_pdf": source_pdf,
            "image_jpg": image_jpg,
        })
    return receipts


def cmd_manifest(args) -> int:
    receipts = parse_batch(args.xlsx)
    print(json.dumps({r["sheet"]: r for r in receipts}, ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_check(args) -> int:
    receipts = parse_batch(args.xlsx)
    period_months = parse_period(args.period) if args.period else None
    warnings = check_batch(receipts, period_months)
    issues = {receipts[i]["sheet"]: w for i, w in warnings.items() if w}
    print(json.dumps(issues, ensure_ascii=False, indent=1))
    return 1 if issues else 0


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("manifest", help="dump per-sheet receipt data as JSON")
    p.add_argument("xlsx", type=Path)
    p.set_defaults(func=cmd_manifest)

    p = sub.add_parser("check", help="run structural checks")
    p.add_argument("xlsx", type=Path)
    p.add_argument("--period", help="reporting period YYYY-MM")
    p.set_defaults(func=cmd_check)

    args = parser.parse_args()
    if not args.xlsx.exists():
        print(f"File not found: {args.xlsx}", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_audit_batch.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/audit_batch.py tests/test_audit_batch.py
git commit -m "feat: audit_batch tool - manifest and check subcommands"
```

---

### Task 4: `agent-prompts` subcommand

**Files:**
- Modify: `tools/audit_batch.py`
- Modify: `tests/test_audit_batch.py` (append tests)

**Interfaces:**
- Consumes: `parse_batch` (Task 3).
- Produces: CLI `agent-prompts <xlsx> [--chunk N] [--scratch DIR]` printing JSON list `[{"label": "audit:R001-R006", "prompt": "..."}]`.

- [ ] **Step 1: Append failing tests to `tests/test_audit_batch.py`**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_audit_batch.py -v -k agent_prompts`
Expected: FAIL — argparse error `invalid choice: 'agent-prompts'` surfaces as non-zero returncode assertion.

- [ ] **Step 3: Implement in `tools/audit_batch.py`**

Add after `cmd_check`:

```python
PROMPT_HEADER = """You are auditing extracted receipt data against the actual receipt \
images for an Israeli tax-reporting pipeline. Hebrew receipts read right-to-left.

For each receipt below, follow this exact order:
1. Read the JPG image with the Read tool and TRANSCRIBE the key printed values \
BEFORE looking at the extracted values: vendor name, vendor tax id (ח.פ/עוסק), \
receipt/invoice number, date, net amount, VAT, total, currency, document type \
(חשבונית / קבלה / חשבונית מס קבלה). Report your transcription.
2. Only then compare your transcription against the extracted values listed for \
that receipt and note real differences.
3. Judge whether the page-1 JPG is representative for human review: does it show \
the vendor and total, or is it a cover page/email with the invoice on a later page?

If the page-1 JPG lacks the needed values, render more pages of the source PDF \
(run from {repo}):
  uv run python -c "import pdf2image; [im.save(rf'{scratch}\\audit_page_{{i}}.jpg','JPEG') \
for i,im in enumerate(pdf2image.convert_from_path(r'<SOURCE_PDF>', dpi=150))]"
then Read those page JPGs.

Do NOT modify any files except temp page renders in the scratch directory.

"""

PROMPT_FOOTER = """
Return one block per receipt, exactly this shape:
SHEET_NAME: VERDICT (OK / MISMATCH / IMAGE-NOT-REPRESENTATIVE / UNVERIFIABLE)
- transcribed: <the values you read off the image>
- mismatches: <field: extracted vs printed - only real differences, or 'none'>
- image_representative: yes/no + what page 1 shows
- notes: <anything odd: possible duplicate, wrong doc type, non-expense document, \
unusual billing entity, unreadable areas>"""


def _receipt_prompt_block(r: dict) -> str:
    info, amounts = r["receipt_info"], r["amounts"]
    return (
        f"{r['sheet']}:\n"
        f"  image: {r['image_jpg']}\n"
        f"  source_pdf: {r['source_pdf']}\n"
        f"  extracted values for comparison (step 2): "
        f"receipt_number={info.get('number')!r}, vendor={info.get('vendor')!r}, "
        f"vendor_id={info.get('vendor_id')!r}, date={info.get('date')!r}, "
        f"doc_type={info.get('document_type')!r}, currency={info.get('currency')!r}, "
        f"net={amounts.get('total_excl_vat')}, vat={amounts.get('vat_amount')}, "
        f"total={amounts.get('total_incl_vat')}, "
        f"category={r['classification'].get('category')!r}\n"
        f"  extracted line items: "
        + "; ".join(
            f"{li['description']} (net={li['amount_excl_vat']}, vat={li['vat']}, "
            f"total={li['total']}, deductible={li['deductible']})"
            for li in r["line_items"]) + "\n"
    )


def cmd_agent_prompts(args) -> int:
    receipts = parse_batch(args.xlsx)
    repo = Path(__file__).parent.parent
    header = PROMPT_HEADER.format(repo=repo, scratch=args.scratch)
    prompts = []
    for i in range(0, len(receipts), args.chunk):
        chunk = receipts[i:i + args.chunk]
        label = (f"audit:{chunk[0]['sheet']}" if len(chunk) == 1
                 else f"audit:{chunk[0]['sheet']}-{chunk[-1]['sheet']}")
        body = "\n".join(_receipt_prompt_block(r) for r in chunk)
        prompts.append({"label": label, "prompt": header + body + PROMPT_FOOTER})
    print(json.dumps(prompts, ensure_ascii=False, indent=1))
    return 0
```

Register in `main()` before `args = parser.parse_args()`:

```python
    p = sub.add_parser("agent-prompts", help="emit visual-verification agent prompts")
    p.add_argument("xlsx", type=Path)
    p.add_argument("--chunk", type=int, default=6)
    p.add_argument("--scratch", default=str(Path.home() / "AppData" / "Local" / "Temp"),
                   help="directory agents may write temp page renders to")
    p.set_defaults(func=cmd_agent_prompts)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_audit_batch.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/audit_batch.py tests/test_audit_batch.py
git commit -m "feat: audit_batch agent-prompts subcommand"
```

---

### Task 5: `apply-fixes` and `verify` subcommands

**Files:**
- Modify: `tools/audit_batch.py`
- Modify: `tests/test_audit_batch.py` (append tests)

**Interfaces:**
- Consumes: `parse_batch`, config helpers.
- Produces: CLI `apply-fixes <xlsx> <fixes.json> --backup-dir DIR` (exit 3 on locked file) and `verify <xlsx>` (exit 1 on integrity problems). fixes.json is a JSON list of entries:
  - `{"sheet": "R001", "field": "vendor", "value": "X", "note": "optional"}` — header field fix
  - `{"sheet": "R011", "line_item": 0, "values": {"description": "...", "amount_excl_vat": 1.0, "vat": 0.2, "total": 1.2}, "note": "optional"}` — 0-based line-item fix
  - `{"sheet": "R022", "non_expense": true, "note": "required"}` — red tab + note

- [ ] **Step 1: Append failing tests**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_audit_batch.py -v -k "apply_fixes or broken"`
Expected: FAIL — `invalid choice: 'apply-fixes'`.

- [ ] **Step 3: Implement in `tools/audit_batch.py`**

Add:

```python
def cmd_apply_fixes(args) -> int:
    fixes = json.loads(Path(args.fixes).read_text(encoding="utf-8"))
    config = get_excel_config()
    field_rows = {field: config.header_start_row + i
                  for i, (_, field) in enumerate(config.get_header_fields())}
    value_col = config.header_value_column
    notes_col = value_col + 2
    note_font = Font(color=AUDIT_NOTE_COLOR, bold=True)

    args.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = args.backup_dir / f"{args.xlsx.stem}.backup-{stamp}.xlsx"
    shutil.copy2(args.xlsx, backup)

    wb = load_workbook(args.xlsx)
    applied = 0
    for fix in fixes:
        ws = wb[fix["sheet"]]
        note_row = None
        if fix.get("non_expense"):
            ws.sheet_properties.tabColor = "FF0000"
            note_row = config.header_start_row
        elif "field" in fix:
            note_row = field_rows[fix["field"]]
            ws.cell(row=note_row, column=value_col, value=fix["value"])
        elif "line_item" in fix:
            row = config.line_items_start_row + fix["line_item"]
            for key, value in fix["values"].items():
                ws.cell(row=row, column=config.get_line_item_column(key), value=value)
        else:
            raise ValueError(f"Unrecognized fix entry: {fix}")
        if fix.get("note") and note_row is not None:
            cell = ws.cell(row=note_row, column=notes_col, value=fix["note"])
            cell.font = note_font
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        applied += 1

    try:
        wb.save(args.xlsx)
    except PermissionError:
        print(f"Cannot save {args.xlsx} - the file is open in Excel. "
              f"Close it and re-run. (Backup kept at {backup})", file=sys.stderr)
        return 3
    print(json.dumps({"applied": applied, "backup": str(backup)}, ensure_ascii=False))
    return 0


def cmd_verify(args) -> int:
    from shared.receipt_checks import AMOUNT_TOLERANCE, _num
    config = get_excel_config()
    wb = load_workbook(args.xlsx)
    problems = {}

    if "CategoryList" not in wb.defined_names:
        problems["workbook"] = ["missing CategoryList named range"]

    fields = [f for _, f in config.get_header_fields()]
    value_col = config.header_value_column
    net_row = config.header_start_row + fields.index("total_excl_vat")
    vat_row = config.header_start_row + fields.index("vat_amount")
    total_row = config.header_start_row + fields.index("total_incl_vat")

    for name in wb.sheetnames:
        if not SHEET_RE.fullmatch(name):
            continue
        ws = wb[name]
        sheet_problems = []
        if len(getattr(ws, "_images", [])) != 1:
            sheet_problems.append(f"{len(ws._images)} embedded images (expected 1)")
        if not ws.data_validations.dataValidation:
            sheet_problems.append("no data validations")
        if not any(c.hyperlink for row in ws.iter_rows() for c in row):
            sheet_problems.append("no source hyperlink")
        net = _num(ws.cell(row=net_row, column=value_col).value)
        vat = _num(ws.cell(row=vat_row, column=value_col).value)
        total = _num(ws.cell(row=total_row, column=value_col).value)
        if total and abs(net + vat - total) > AMOUNT_TOLERANCE:
            sheet_problems.append(f"arithmetic: {net:g} + {vat:g} != {total:g}")
        if sheet_problems:
            problems[name] = sheet_problems

    print(json.dumps(problems, ensure_ascii=False, indent=1))
    return 1 if problems else 0
```

Register in `main()`:

```python
    p = sub.add_parser("apply-fixes", help="apply a fixes.json to the workbook")
    p.add_argument("xlsx", type=Path)
    p.add_argument("fixes", type=Path)
    p.add_argument("--backup-dir", type=Path, required=True)
    p.set_defaults(func=cmd_apply_fixes)

    p = sub.add_parser("verify", help="post-fix integrity check")
    p.add_argument("xlsx", type=Path)
    p.set_defaults(func=cmd_verify)
```

Adjust `run_cli` assertion in the test file if needed: apply-fixes returns 0, verify returns 0/1.

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/audit_batch.py tests/test_audit_batch.py
git commit -m "feat: audit_batch apply-fixes and verify subcommands"
```

---

### Task 6: Validation gate against the real 2026-07-11 batch

Manual run-and-observe against the preserved pre-fix backup (contains real financial data — never commit it). Ground truth from the 2026-07-11 audit session.

- [ ] **Step 1: Copy the pre-fix backup to a work location**

```bash
cp "C:\Users\micha\AppData\Local\Temp\claude\D--code-receipt-processing-israel\3821df14-47f8-470d-84c7-c66f9e55b1fc\scratchpad\receipts_batch_001_BACKUP.xlsx" "C:\Users\micha\AppData\Local\Temp\claude\D--code-receipt-processing-israel\3821df14-47f8-470d-84c7-c66f9e55b1fc\scratchpad\validation_batch.xlsx"
```

(If the scratchpad backup no longer exists, skip this task and note it in the summary — the synthetic tests still gate the code.)

- [ ] **Step 2: `check --period 2026-05` must flag the known issues**

Run: `PYTHONIOENCODING=utf-8 uv run python tools/audit_batch.py check <validation_batch> --period 2026-05`
Expected (exit 1) — at minimum:
- `R011` (Weezmo): zero total, missing number, missing date, missing vendor_id
- `R002` (AcmeMobile): missing vendor_id
- `R022` (Section 46): zero total, date 2022-07-12 out of period
- `R018`: line items sum 190 ≠ 210

- [ ] **Step 3: `manifest` and `agent-prompts` values match the workbook**

Run manifest; spot-check R001 shows number `<misread-receipt-number>` (the original misread — this is the pre-fix file), total `<recorded-total>`. Run agent-prompts; confirm the same values appear verbatim in the prompt text.

- [ ] **Step 4: Replay session fixes via `apply-fixes` + `verify`**

Build `fixes.json` reproducing the session's R001/R002/R011 corrections (number `<corrected-receipt-number>`, vendor `<corrected-vendor-name>`, R002 vendor_id `<vendor-reg-number>`, R011 full re-entry incl. line item), apply, then `verify` must exit 0 and `check` must no longer flag R011 amounts/date/number (vendor_id warnings for sheets that legitimately lack it may remain).

- [ ] **Step 5: Record results**

Note pass/fail per expectation in the execution summary. No commit (nothing tracked changes).

---

### Task 7: The skill — `.claude/skills/bimonthly-cycle/SKILL.md`

**Files:**
- Create: `.claude/skills/bimonthly-cycle/SKILL.md`

**Interfaces:**
- Consumes: all `audit_batch.py` subcommands (exact invocations), `AUDIT_KNOWLEDGE.personal.md` (Task 8).

- [ ] **Step 1: Write the skill file**

Create `.claude/skills/bimonthly-cycle/SKILL.md` with exactly this content:

````markdown
---
name: bimonthly-cycle
description: Run the bi-monthly Israeli receipt cycle - extract receipts with OpenAI, audit the extraction batch against source documents, hand off for human review, consolidate to iCount format, and reflect. Use when the user wants to run the bi-monthly cycle, extract a folder of receipts, audit an extraction batch xlsx, or consolidate reviewed batches.
---

# Bi-Monthly Receipt Cycle

Five phases. Announce the current phase. Never skip the audit.
All commands run from the repo root with `PYTHONIOENCODING=utf-8`.

**Corrections log:** the moment the user corrects ANYTHING (a value, a category,
a process step), append one line to `corrections.md` in the session scratchpad:
`- [phase] what the user corrected, and what we had wrong`. Phase 5 consumes it.

**Personal context:** read `AUDIT_KNOWLEDGE.personal.md` (repo root, untracked)
before Phase 2. It lists known-OK anomalies and business context. If it is
missing, ask the user whether to recreate it.

## Phase 1 - Extract

1. Ask for the raw-docs folder and the reporting period (YYYY-MM) if not given.
2. `uv run python receipt_extractor.py "<folder>" --period <YYYY-MM>`
3. Confirm from the summary that every file processed; extraction failures get
   an empty batch for manual entry - list them for the user.
4. Red-tabbed sheets were flagged by generation-time sanity checks; they get
   priority attention in Phase 2.

## Phase 2 - Audit

The audit exists because it is INDEPENDENT of extraction. Never weaken that:

- **HARD RULE: never hand-type extracted values into agent prompts.** Use
  `agent-prompts` output verbatim. (Hand-typed values once fabricated three
  false bugs that survived until a backup comparison exposed them.)
- **HARD RULE: back up before editing, `verify` after.** `apply-fixes` does
  both; do not edit the workbook with ad-hoc scripts.
- Agents transcribe the image FIRST, then compare - the prompts enforce this;
  do not reorder.

Steps (`BATCH` = the batch xlsx path):

1. `uv run python tools/audit_batch.py check BATCH --period <YYYY-MM>` and
   `... manifest BATCH` - note structural issues.
2. `uv run python tools/audit_batch.py agent-prompts BATCH --scratch <session scratchpad>`
   then dispatch each prompt to a general-purpose subagent (parallel, background).
3. Reconcile agent transcriptions against the manifest. Consult
   `AUDIT_KNOWLEDGE.personal.md` - known anomalies are not findings.
   `UNVERIFIABLE` verdicts: read the image yourself; re-dispatch a dead agent's
   chunk once before reading directly.
4. Write `fixes.json` (schema in `tools/audit_batch.py` docstring/tests):
   corrected values with Hebrew audit notes, `non_expense: true` for documents
   that are not expenses (certificates, confirmations - do not delete sheets).
5. `uv run python tools/audit_batch.py apply-fixes BATCH fixes.json --backup-dir <session scratchpad>`
   - exit 3 means the file is open in Excel: ask the user to close it, retry once.
6. `uv run python tools/audit_batch.py verify BATCH` must exit 0.
7. Report to the user: values fixed (with evidence), items needing their
   judgment (billing entity, deductibility), suspected non-expense documents.

## Phase 3 - Human review

Hand off: the user reviews the audited xlsx in Excel (red tabs first, then
audit notes in the הערות column). When they say they are done:
`uv run python tools/audit_batch.py check BATCH --period <YYYY-MM>` again -
their edits are unvalidated input. Surface any new issues before Phase 4.

## Phase 4 - Consolidate

1. `uv run python receipt_consolidator.py BATCH [BATCH2 ...]`
2. Verify the output XLS: row count equals audited sheets minus non-expense
   ones the user removed; receipts with non-deductible line items import the
   deductible portion only (the consolidator logs these); receipt files copied
   with standardized names.
3. Remind the user: non-expense sheets (red tab) must be deleted from the xlsx
   BEFORE consolidation, or removed from the iCount file after - confirm which
   happened.

## Phase 5 - Reflect

Walk `corrections.md` from the scratchpad plus anything you remember and
propose routed updates, each requiring explicit user approval:

- Personal facts (billing entities, vendor quirks, known anomalies) →
  `AUDIT_KNOWLEDGE.personal.md`
- General process lessons → this SKILL.md (edit it)
- Extraction steering (categories, deductibility, vendor rules) →
  `docs/extraction-prompt/002-ADDITIONAL_INSTRUCTIONS.personal.md`

Nothing is written without approval. Do not auto-commit SKILL.md edits -
show the diff and let the user decide.

## Known traps

- openpyxl ignores `img.width`/`img.height` on loaded workbooks - image sizing
  goes through `anchor.ext` (audit_batch handles this; never bypass it).
- `PermissionError` on save = file open in Excel.
- Windows console is cp1252 - Hebrew output needs `PYTHONIOENCODING=utf-8`.
- Weezmo/EZcount/invoice4u are receipt-delivery platforms, never the vendor.
- A receipt's printed total may differ from net+VAT by one agora - that is
  receipt rounding, not an extraction error (tolerance 0.02).
````

- [ ] **Step 2: Sanity-check the skill loads**

Run: `ls .claude/skills/bimonthly-cycle/SKILL.md` and read the frontmatter — well-formed YAML, name matches directory.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/bimonthly-cycle/SKILL.md
git commit -m "feat: bimonthly-cycle skill"
```

---

### Task 8: Seed `AUDIT_KNOWLEDGE.personal.md` + docs

**Files:**
- Create: `AUDIT_KNOWLEDGE.personal.md` (untracked — verify)
- Modify: `CLAUDE.md` (brief additions)

- [ ] **Step 1: Create `AUDIT_KNOWLEDGE.personal.md`**

The file's real content is personal and stays out of this public repo (it is
seeded from the user's actual audit history). Structure to create:

```markdown
# Audit Knowledge (personal, untracked)

Consulted by Claude during Phase 2 of the bimonthly-cycle skill.
Known-OK anomalies listed here are NOT findings. Updated via Phase 5 reflection.

## Business context

- <billing arrangements that look anomalous but are known-OK, e.g. invoices
  billed to a second entity that belong to this business>

## Vendor notes

- <per-vendor quirks: expected 0%-VAT foreign invoices, combined bills where
  only some lines are deductible, document-number conventions, fixed
  vendor→category rules, low-res receipt sources>

## Known non-expenses

- <document types that look like receipts but are not expenses (e.g. validity
  certificates) - flag non_expense>
```

- [ ] **Step 2: Verify it is untracked**

Run: `git check-ignore -v AUDIT_KNOWLEDGE.personal.md`
Expected: matches the `*.personal.md` gitignore rule. If not, STOP and fix `.gitignore` before proceeding.

- [ ] **Step 3: Update `CLAUDE.md`**

In the Project Overview section, after the VAT Report line, add:

```markdown
**Audit tooling**: `tools/audit_batch.py` - manifest/check/agent-prompts/apply-fixes/verify subcommands over extraction batch xlsx files; structural checks shared with the extractor via `shared/receipt_checks.py`. The `bimonthly-cycle` project skill orchestrates the full cycle. `AUDIT_KNOWLEDGE.personal.md` (untracked) holds personal audit context.
```

In Important Notes, replace the line `- No test suite currently exists` with:

```markdown
- Tests: `uv run pytest tests/` (covers receipt_checks and audit_batch; no coverage for extractor API paths)
```

- [ ] **Step 4: Run full test suite one last time**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: audit tooling and test suite notes in CLAUDE.md"
```
