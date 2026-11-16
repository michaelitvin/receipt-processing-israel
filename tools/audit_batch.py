#!/usr/bin/env python3
# Copyright (c) 2025 Michael Litvin
# Licensed under AGPL-3.0-or-later - see LICENSE file for details
"""Deterministic audit tooling for extraction batch workbooks.

Subcommands (JSON in/out, UTF-8):
  manifest <xlsx>                       dump per-sheet receipt data
  check <xlsx> [--period YYYY-MM]      structural checks (exit 1 if issues)
  textcheck <xlsx> [--sources-dir DIR]  extracted values must appear in the source
                                        PDF's text layer (exit 1 on findings); lists
                                        sheets with no text layer under "visual"
  agent-prompts <xlsx> [--chunk N] [--sheets R004 ...]
                                        BLIND transcription prompts (no extracted
                                        values) for the "visual" sheets
  reconcile <xlsx> <transcriptions.json>
                                        compare agents' JSON transcriptions with the
                                        workbook in code (exit 1 on findings)
  apply-fixes <xlsx> <fixes.json> --backup-dir DIR
  verify <xlsx> [--fixes fixes.json]   post-fix integrity (exit 1 if broken); with
                                        --fixes, also checks each fix landed
  recurring <xlsx>...                   warn if an expected recurring vendor is
                                        missing (reads RECURRING_VENDORS.personal.yaml)
  history <xlsx>... [--period YYYY-MM] [--past DIR ...]
                                        compare against every icount_import_*.xls ever
                                        filed: duplicates (exit 1), name/id/category
                                        drift, vendors seen before but absent now
  merge <out.xlsx> <xlsx>... [--drop R009 ...]
                                        combine batches into one workbook (sheet names
                                        and audit notes preserved), dropping sheets

fixes.json is a JSON list; each entry is one of:
  {"sheet": "R001", "field": "vendor", "value": "X", "note": "optional"}
  {"sheet": "R003", "field": "vendor_id", "value": null, "note": "optional"}
      "value": null CLEARS the cell (the only way to blank a field). A missing
      "value" or an empty string "" is rejected, so nothing is blanked by accident.
  {"sheet": "R011", "line_item": 0,
   "values": {"description": "...", "amount_excl_vat": 1.0, "vat": 0.2, "total": 1.2},
   "note": "optional"}
      a null in "values" clears that line-item cell; "" is rejected the same way
  {"sheet": "R022", "non_expense": true, "note": "required"}
  {"sheet": "R005", "resolved": true, "note": "optional"}
      clears the red tab and the generator's review warning once the audit
      has settled every flag on the sheet

Used by the bimonthly-cycle skill; see .claude/skills/bimonthly-cycle/SKILL.md.
"""
import argparse
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
from copy import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font

from shared.excel_config import get_excel_config
from shared.receipt_checks import (_num, check_batch, missing_recurring_vendors,
                                   parse_period)
from shared.personal_config import get_own_tax_ids

load_dotenv(Path(__file__).parent.parent / ".env")

# Sheet names may carry a human-added vendor suffix after review (e.g. "R001partner")
SHEET_RE = re.compile(r"R\d{3}.*")
LINE_ITEMS_SUM_LABEL = get_excel_config().line_items_sum_label
AUDIT_NOTE_COLOR = "CC5500"
# ExcelGenerator._add_review_warnings writes this prefix before the warnings
REVIEW_WARNING_PREFIX = "לבדיקה ידנית"

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
                # Excel re-saves absolute links percent-encoded - decode them
                source_pdf = unquote(re.sub(r"^file:///", "", cell.hyperlink.target)).replace("/", "\\")
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


def _require_sheets(receipts: list) -> None:
    """Guard against a workbook whose receipt sheets none of us can see.

    A reviewed batch with renamed/reordered sheets used to parse to zero receipts
    and pass every check silently. Fail loudly instead.
    """
    if not receipts:
        print(json.dumps(
            {"workbook": ["no R### receipt sheets parsed - wrong file or unreadable layout"]},
            ensure_ascii=False, indent=1))
        raise SystemExit(2)


def cmd_manifest(args) -> int:
    receipts = parse_batch(args.xlsx)
    _require_sheets(receipts)
    print(json.dumps({r["sheet"]: r for r in receipts}, ensure_ascii=False, indent=1, default=str))
    return 0


RECURRING_VENDORS_FILE = Path(__file__).parent.parent / "RECURRING_VENDORS.personal.yaml"


def _load_recurring_vendors() -> list:
    """Load the personal recurring-vendors list, or [] if it is absent."""
    if not RECURRING_VENDORS_FILE.exists():
        return []
    import yaml
    data = yaml.safe_load(RECURRING_VENDORS_FILE.read_text(encoding="utf-8")) or {}
    return data.get("recurring") or []


def cmd_recurring(args) -> int:
    recurring = _load_recurring_vendors()
    if not recurring:
        print(json.dumps(
            {"note": f"no {RECURRING_VENDORS_FILE.name} - nothing to check"},
            ensure_ascii=False, indent=1))
        return 0
    receipts = []
    for xlsx in args.xlsx:
        receipts.extend(parse_batch(Path(xlsx)))
    _require_sheets(receipts)
    missing = missing_recurring_vendors(receipts, recurring)
    present = [e.get("name") for e in recurring if e.get("name") not in missing]
    print(json.dumps({"missing": missing, "present": present,
                      "batches": len(args.xlsx), "receipts": len(receipts)},
                     ensure_ascii=False, indent=1))
    return 1 if missing else 0


# iCount import columns read back by `history` (located by header text, like vat_report)
ICOUNT_IMPORT_COLS = {"id": "תז/חפ הספק", "name": "שם הספק", "category": "שם סוג הוצאה",
                      "amount": "סכום", "number": "מספר מסמך", "date": "תאריך האסמכתא"}
_NAME_NOISE_RE = re.compile(r"[\"'״׳’.,()\-\s]")
_NO_VAT_SUFFIX_RE = re.compile(r"\s*\(ללא מע.מ\)$")


def _norm_name(s) -> str:
    """Vendor name for comparison: the consolidator strips double quotes on export,
    so בע"מ and בעמ must compare equal; punctuation/spacing differences are noise."""
    return _NAME_NOISE_RE.sub("", str(s or "")).lower()


def _vendor_key(vendor_id, name) -> tuple:
    vid = str(vendor_id or "").strip()
    return ("id", vid) if vid else ("name", _norm_name(name))


def _period_bucket(date_str: str) -> str:
    """Bi-monthly reporting bucket of a YYYY-MM-DD date, e.g. 2026-06-17 -> 2026-05."""
    m = re.match(r"(\d{4})-(\d{2})", str(date_str or ""))
    if not m:
        return ""
    return f"{m.group(1)}-{(int(m.group(2)) - 1) // 2 * 2 + 1:02d}"


def _import_stamp(f: Path) -> Optional[str]:
    """YYYYMMDD from icount_import_YYYYMMDD_HHMMSS.xls; None when the name carries
    no stamp (mtime is not trusted - copying an archive resets it)."""
    m = re.search(r"icount_import_(\d{8})_", f.name)
    return m.group(1) if m else None


def _period_end_stamp(period: str) -> str:
    """Last day of the bi-monthly period as YYYYMMDD (2026-07 -> 20260831)."""
    y, m = map(int, max(parse_period(period)).split("-"))
    nxt = datetime(y + (m == 12), m % 12 + 1, 1)
    return (nxt - timedelta(days=1)).strftime("%Y%m%d")


def _load_past_imports(dirs, period: Optional[str] = None) -> tuple:
    """Every row of every icount_import_*.xls under dirs (recursive).

    Identical rows are collapsed (a consolidation re-run writes the same rows to a
    second file); the first file that carried a row is kept as provenance. With a
    period, imports produced AFTER that period ended are skipped - they are this
    period's own consolidation (or later), not history - and returned as excluded.
    """
    import xlrd
    rows, seen, excluded = [], set(), []
    cutoff = _period_end_stamp(period) if period else None
    for d in dirs:
        for f in sorted(Path(d).rglob("icount_import_*.xls")):
            stamp = _import_stamp(f)
            if cutoff and stamp and stamp > cutoff:
                excluded.append(f.name)
                continue
            ws = xlrd.open_workbook(f).sheet_by_index(0)
            header = [str(ws.cell_value(0, c)).strip() for c in range(ws.ncols)]
            missing = [h for h in ICOUNT_IMPORT_COLS.values() if h not in header]
            if missing:
                raise SystemExit(f"{f}: iCount import columns not found: {missing}")
            idx = {k: header.index(h) for k, h in ICOUNT_IMPORT_COLS.items()}
            for r in range(1, ws.nrows):
                row = {k: str(ws.cell_value(r, i)).strip() for k, i in idx.items()}
                row["category"] = _NO_VAT_SUFFIX_RE.sub("", row["category"])
                row["file"] = f.name
                key = (row["id"], _norm_name(row["name"]), row["number"], row["amount"], row["date"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    return rows, excluded


def _default_past_dirs() -> list:
    from shared.personal_config import get_past_imports_dirs
    repo = Path(__file__).parent.parent
    dirs = [repo / "receipts_consolidated"] + [Path(d) for d in get_past_imports_dirs()]
    return [d for d in dirs if d.exists()]


def cmd_history(args) -> int:
    """Compare the period's batches against everything already imported to iCount.

    Reports (JSON): receipts that look already filed (duplicates - exit 1), vendors
    whose name/id/category drifted from past filings, vendors with no history, and
    vendors seen in the last filed period (or in 3+ periods) that are absent now.
    """
    receipts = []
    for xlsx in args.xlsx:
        receipts.extend(parse_batch(Path(xlsx)))
    _require_sheets(receipts)
    dirs = [Path(d) for d in args.past] if args.past else _default_past_dirs()
    past, excluded = _load_past_imports(dirs, args.period)
    if not past:
        print(json.dumps({"note": f"no icount_import_*.xls found under {[str(d) for d in dirs]}"},
                         ensure_ascii=False, indent=1))
        return 0

    from collections import Counter, defaultdict
    by_key = defaultdict(list)
    for p in past:
        by_key[_vendor_key(p["id"], p["name"])].append(p)
        if p["id"]:
            by_key[("name", _norm_name(p["name"]))].append(p)

    duplicates, inconsistent, new_vendors, matched_keys = [], [], [], set()
    for r in receipts:
        ri, am = r["receipt_info"], r["amounts"]
        cat = r["classification"].get("category", "")
        key = _vendor_key(ri["vendor_id"], ri["vendor"])
        hist = by_key.get(key) or by_key.get(("name", _norm_name(ri["vendor"])), [])
        if not hist:
            new_vendors.append({"sheet": r["sheet"], "vendor": ri["vendor"],
                                "vendor_id": ri["vendor_id"], "category": cat})
            continue
        for p in hist:
            matched_keys.add(_vendor_key(p["id"], p["name"]))
            same_number = ri["number"] and _norm_name(ri["number"]) == _norm_name(p["number"])
            same_amount_date = (ri["date"] == p["date"][:10] and
                                abs(_num(am["total_incl_vat"]) - _num(p["amount"])) < 0.01)
            if same_number or same_amount_date:
                duplicates.append({"sheet": r["sheet"], "number": ri["number"],
                                   "vendor": ri["vendor"], "already_filed": {
                                       "file": p["file"], "vendor": p["name"], "number": p["number"],
                                       "amount": p["amount"], "date": p["date"]}})
        names = Counter(p["name"] for p in hist)
        ids = Counter(p["id"] for p in hist)
        cats = Counter(p["category"] for p in hist)
        if _norm_name(ri["vendor"]) not in {_norm_name(n) for n in names}:
            inconsistent.append({"sheet": r["sheet"], "field": "vendor",
                                 "now": ri["vendor"], "past": dict(names)})
        if str(ri["vendor_id"] or "") not in ids:
            inconsistent.append({"sheet": r["sheet"], "field": "vendor_id",
                                 "now": ri["vendor_id"], "past": dict(ids)})
        if cat not in cats:
            inconsistent.append({"sheet": r["sheet"], "field": "category",
                                 "now": cat, "past": dict(cats)})

    buckets_by_key = defaultdict(set)
    for p in past:
        b = _period_bucket(p["date"])
        if b:
            buckets_by_key[_vendor_key(p["id"], p["name"])].add(b)
    last_bucket = max((b for s in buckets_by_key.values() for b in s), default="")
    missing = []
    for k, buckets in buckets_by_key.items():
        if k in matched_keys:
            continue
        if last_bucket in buckets or len(buckets) >= 3:
            rows = [p for p in past if _vendor_key(p["id"], p["name"]) == k]
            latest = max(rows, key=lambda p: p["date"])
            missing.append({"vendor": Counter(p["name"] for p in rows).most_common(1)[0][0],
                            "vendor_id": k[1] if k[0] == "id" else "",
                            "periods_seen": len(buckets),
                            "in_last_filed_period": last_bucket in buckets,
                            "last_date": latest["date"], "last_amount": latest["amount"],
                            "category": latest["category"]})
    missing.sort(key=lambda m: (-m["in_last_filed_period"], -m["periods_seen"], m["vendor"]))

    print(json.dumps({"past_rows": len(past), "past_dirs": [str(d) for d in dirs],
                      "excluded_files": excluded,
                      "last_filed_period": last_bucket, "duplicates": duplicates,
                      "inconsistent": inconsistent, "new_vendors": new_vendors,
                      "missing_vs_previous": missing}, ensure_ascii=False, indent=1))
    return 1 if duplicates else 0


CATEGORIES_FILE = Path(__file__).parent.parent / "docs" / "extraction-prompt" / "001-icount-categories.md"


def cmd_merge(args) -> int:
    """Combine several batch workbooks into one (e.g. a period's main batch plus a
    late supplement), optionally dropping sheets (a duplicate flagged non_expense).

    The output is regenerated through ExcelGenerator from the parsed sheets - the
    only code path that lays out a review sheet and embeds its image - so it is a
    first-class batch (verify/check/consolidate all work on it). Sheet names are
    preserved; a later source's sheet whose name is already taken gets the next
    free R### number, so audit notes and the user's references stay valid. Column
    D (audit notes / review warnings) and tab colors are carried over.
    """
    from shared.excel_generator import ExcelGenerator
    from openpyxl.utils import get_column_letter

    config = get_excel_config()
    drop = set(args.drop or [])
    out = Path(args.out)
    images_dir = out.parent / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    receipts, names, mapping, dropped = [], [], {}, []
    taken = set()
    next_free = 1
    for i, xlsx in enumerate(args.xlsx):
        src = Path(xlsx)
        src_wb = load_workbook(src)
        src_key = f"{i}:{src.name}"
        mapping[src_key] = {}
        for r in parse_batch(src):
            m = re.match(r"R(\d{3})", r["sheet"])
            next_free = max(next_free, int(m.group(1)) + 1)
            if r["sheet"] in drop:
                dropped.append(f"{src_key}:{r['sheet']}")
                continue
            name = r["sheet"]
            if name in taken:
                name = config.get_worksheet_name(next_free)
                next_free += 1
            taken.add(name)
            mapping[src_key][r["sheet"]] = name
            # the hyperlink target is the absolute source path; the generator
            # derives both the link and the image filename from original_file
            r["receipt_info"]["original_file"] = r["source_pdf"] or r["receipt_info"]["original_file"]
            jpg = Path(r["image_jpg"])
            if jpg.exists():
                dest = images_dir / jpg.name
                if dest.resolve() != jpg.resolve():
                    if dest.exists() and not filecmp.cmp(jpg, dest, shallow=False):
                        raise SystemExit(f"image name collision across batches: {jpg.name}")
                    shutil.copy2(jpg, dest)
            receipts.append(r)
            names.append((name, src_wb[r["sheet"]]))

    _require_sheets(receipts)
    wb = ExcelGenerator(CATEGORIES_FILE).create_batch_workbook(receipts, images_dir)

    # rename generated R001.. to the preserved names (two passes: targets may
    # collide with not-yet-renamed generated sheets)
    generated = [config.get_worksheet_name(i) for i in range(1, len(names) + 1)]
    for i, g in enumerate(generated):
        wb[g].title = f"tmp{i:03d}"
    notes_col = config.header_value_column + 2
    for i, (name, src_ws) in enumerate(names):
        ws = wb[f"tmp{i:03d}"]
        ws.title = name
        ws.sheet_properties.tabColor = src_ws.sheet_properties.tabColor
        for row in range(1, config.line_items_start_row):
            cell = src_ws.cell(row=row, column=notes_col)
            if cell.value is None:
                continue
            dst = ws.cell(row=row, column=notes_col, value=cell.value)
            dst.font = copy(cell.font)
            dst.alignment = copy(cell.alignment)

    try:
        wb.save(out)
    except PermissionError:
        print(f"Cannot save {out} - the file is open in Excel.", file=sys.stderr)
        return 3
    print(json.dumps({"out": str(out), "sheets": len(names), "dropped": dropped,
                      "mapping": mapping, "images_dir": str(images_dir)},
                     ensure_ascii=False, indent=1))
    return 0


def cmd_check(args) -> int:
    receipts = parse_batch(args.xlsx)
    _require_sheets(receipts)
    period_months = parse_period(args.period) if args.period else None
    own_ids = get_own_tax_ids()
    warnings = check_batch(receipts, period_months, own_ids)
    issues = {receipts[i]["sheet"]: w for i, w in warnings.items() if w}
    print(json.dumps(issues, ensure_ascii=False, indent=1))
    return 1 if issues else 0


PROMPT_HEADER = """You are transcribing receipt documents for an Israeli tax-reporting \
audit. Hebrew receipts read right-to-left. You are NOT shown what was extracted \
from these documents - your reading is compared against it by code afterwards, \
so read every value off the document itself.

For each receipt below:
1. Read the JPG image with the Read tool. If it lacks the needed values (cover \
page, email body, cropped), render more pages of the source PDF (run from {repo}):
  uv run python -c "import pdf2image; [im.save(rf'{scratch}\\audit_page_{{i}}.jpg','JPEG') \
for i,im in enumerate(pdf2image.convert_from_path(r'<SOURCE_PDF>', dpi=150))]"
   then Read those page JPGs. For long digit strings that are hard to read, \
crop and enlarge the region with PIL (save the crop in the scratch directory) \
and Read the crop.
2. Copy numbers CHARACTER BY CHARACTER and count their digits. Never round, \
never reformat a number, never guess a digit - use null for anything you \
cannot read with certainty.

Do NOT modify any files except temp renders/crops in the scratch directory: {scratch}

"""

PROMPT_FOOTER = """
Return ONLY a JSON array (in a ```json fence), one object per receipt, keys:
  "sheet": the sheet name given above
  "verdict": "READ" | "UNVERIFIABLE" (cannot read the key values) | \
"IMAGE-NOT-REPRESENTATIVE" (page 1 JPG does not show vendor and total)
  "vendor": vendor name as printed
  "vendor_ids": [every business id printed for the SELLER: ח.פ, ע.מ, עוסק מורשה, \
VAT no.] - not the buyer's id
  "number": the document's own number (חשבונית / קבלה / invoice / receipt no.)
  "number_digits": count of digits in "number"
  "other_numbers": [every other long reference printed: UID, transaction/עסקה, \
voucher/שובר, order, account numbers] - exactly as printed
  "date": document date as YYYY-MM-DD
  "currency": "ILS" | "USD" | "EUR" | ...
  "document_type": as printed (e.g. חשבונית מס קבלה, Receipt, Invoice)
  "total_excl_vat", "vat_amount", "total_incl_vat": numbers as printed \
(the amount actually charged/paid on this document)
  "notes": anything odd - possible duplicate, non-expense document (certificate, \
confirmation), billing entity, partial payment, unreadable areas
Use null for any value not printed or not readable."""


def _receipt_prompt_block(r: dict) -> str:
    # Blind by design: paths only, never extracted values (see PROMPT_HEADER).
    return (f"{r['sheet']}:\n"
            f"  image: {r['image_jpg']}\n"
            f"  source_pdf: {r['source_pdf']}\n")


def cmd_agent_prompts(args) -> int:
    receipts = parse_batch(args.xlsx)
    _require_sheets(receipts)
    if getattr(args, "sheets", None):
        receipts = [r for r in receipts if r["sheet"] in set(args.sheets)]
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


# --- reconcile: compare blind transcriptions against the workbook in code ---

RECONCILE_AMOUNTS = ("total_excl_vat", "vat_amount", "total_incl_vat")


def _ref_key(v) -> str:
    """Normalize a reference number/id for comparison: alphanumerics only, upper."""
    return re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _ref_finding(field: str, extracted, candidates: list) -> Optional[dict]:
    """None if `extracted` equals one of the printed candidates; otherwise a finding.

    near_miss = within 2 edits of a printed value (a dropped/extra/swapped digit):
    a typo to fix, not a judgment call.
    """
    ext = _ref_key(extracted)
    cands = [c for c in (_ref_key(x) for x in candidates) if c]
    if not ext and not cands:
        return None
    if not ext:
        return {"field": field, "kind": "missing", "extracted": extracted, "printed": candidates}
    if ext in cands:
        return None
    if not cands:
        return {"field": field, "kind": "not_on_document", "extracted": extracted}
    best = min(cands, key=lambda c: _edit_distance(ext, c))
    kind = "near_miss" if _edit_distance(ext, best) <= 2 else "mismatch"
    return {"field": field, "kind": kind, "extracted": extracted, "printed": best,
            "digits": {"extracted": len(re.sub(r"\D", "", ext)),
                       "printed": len(re.sub(r"\D", "", best))}}


def _reconcile_one(r: dict, t: dict) -> list:
    info, amounts = r["receipt_info"], r["amounts"]
    findings = []
    for f in (_ref_finding("number", info.get("number"),
                           [t.get("number")] + list(t.get("other_numbers") or [])),
              _ref_finding("vendor_id", info.get("vendor_id"), list(t.get("vendor_ids") or []))):
        if f:
            findings.append(f)
    if t.get("date") and str(info.get("date") or "")[:10] != t["date"]:
        findings.append({"field": "date", "kind": "mismatch",
                         "extracted": info.get("date"), "printed": t["date"]})
    if t.get("currency") and str(info.get("currency") or "").upper() != str(t["currency"]).upper():
        findings.append({"field": "currency", "kind": "mismatch",
                         "extracted": info.get("currency"), "printed": t["currency"]})
    for k in RECONCILE_AMOUNTS:
        printed = t.get(k)
        if printed is None:
            continue
        if abs(_num(amounts.get(k)) - _num(printed)) > 0.01:
            findings.append({"field": k, "kind": "mismatch",
                             "extracted": amounts.get(k), "printed": printed})
    return findings


def cmd_reconcile(args) -> int:
    receipts = {r["sheet"]: r for r in parse_batch(args.xlsx)}
    _require_sheets(list(receipts.values()))
    transcriptions = json.loads(Path(args.transcriptions).read_text(encoding="utf-8"))
    out = {"matched": [], "findings": {}, "unverifiable": {}, "not_representative": {},
           "unknown_sheets": [], "not_transcribed": []}
    seen = set()
    for t in transcriptions:
        sheet = t.get("sheet")
        if sheet not in receipts:
            out["unknown_sheets"].append(sheet)
            continue
        seen.add(sheet)
        verdict = str(t.get("verdict") or "READ").upper()
        if verdict == "UNVERIFIABLE":
            out["unverifiable"][sheet] = t.get("notes") or ""
            continue
        if verdict == "IMAGE-NOT-REPRESENTATIVE":
            out["not_representative"][sheet] = t.get("notes") or ""
        found = _reconcile_one(receipts[sheet], t)
        if found:
            out["findings"][sheet] = found
        else:
            out["matched"].append(sheet)
    out["not_transcribed"] = [s for s in receipts if s not in seen]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    bad = out["findings"] or out["unverifiable"] or out["unknown_sheets"]
    return 1 if bad else 0


# --- textcheck: extracted values must appear in the source PDF's text layer ---

MIN_TEXT_LAYER_CHARS = 30
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _pdf_text(path: Path) -> str:
    """pdftotext -layout via an ASCII-named temp copy (poppler on Windows cannot
    open non-ASCII paths, and a failed open looks exactly like an empty text layer)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "source.pdf"
        shutil.copyfile(path, tmp)
        result = subprocess.run(["pdftotext", "-layout", str(tmp), "-"], capture_output=True)
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", "replace")


def _present(token: str, haystacks: list) -> bool:
    pat = re.compile(rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])", re.IGNORECASE)
    return any(pat.search(h) for h in haystacks)


def _amount_present(value, haystacks: list) -> bool:
    x = _num(value)
    forms = {f"{x:.2f}", f"{x:,.2f}"}
    if x == int(x):
        forms |= {str(int(x)), f"{int(x):,}"}
    pats = [re.compile(rf"(?<![\d.,]){re.escape(f)}(?![\d]|[.,]\d)") for f in forms]
    return any(p.search(h) for p in pats for h in haystacks)


def _date_present(date_str: str, haystacks: list) -> bool:
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return False
    dd, mm, yyyy, yy = f"{d.day:02d}", f"{d.month:02d}", str(d.year), str(d.year)[2:]
    forms = {f"{dd}{s}{mm}{s}{y}" for s in "/.-" for y in (yyyy, yy)}
    forms |= {f"{d.day}/{d.month}/{yyyy}", f"{yyyy}-{mm}-{dd}", f"{yyyy}/{mm}/{dd}"}
    if any(_present(f, haystacks) for f in forms):
        return True
    mon = _MONTHS[d.month - 1]
    pats = [re.compile(rf"\b{mon}[a-z]*\.? {d.day},? {yyyy}\b", re.IGNORECASE),
            re.compile(rf"\b{d.day} {mon}[a-z]*\.?,? {yyyy}\b", re.IGNORECASE)]
    return any(p.search(h) for p in pats for h in haystacks)


def text_findings(r: dict, text: str) -> list:
    """Values of receipt `r` that do not appear in `text` (a PDF text layer).

    Matching is boundary-strict, so an extracted number with a digit missing does
    not 'match' inside the longer printed one.
    """
    text = text.replace("‎", "").replace("‏", "")
    flat = re.sub(r"\s+", " ", text)
    joined = re.sub(r"(?<=\d)[ -](?=\d)", "", flat)  # "51-123456-7" / "5123 4567"
    hay = [flat, joined]
    info, amounts = r["receipt_info"], r["amounts"]
    out = []
    for field in ("number", "vendor_id"):
        v = str(info.get(field) or "").strip()
        if v and not (_present(v, hay) or _present(_ref_key(v), hay)):
            out.append(f"{field}={v} not in text")
    d = str(info.get("date") or "")
    if d and not _date_present(d, hay):
        out.append(f"date={d[:10]} not in text")
    for k in RECONCILE_AMOUNTS:
        v = amounts.get(k)
        if _num(v) and not _amount_present(v, hay):
            out.append(f"{k}={v} not in text")
    li_total = round(sum(_num(li.get("total")) for li in r["line_items"]), 2)
    if r["line_items"] and abs(li_total - _num(amounts.get("total_incl_vat"))) > 0.02:
        out.append(f"line items sum {li_total} != total_incl_vat")
    return out


def _resolve_source(r: dict, sources_dir: Optional[Path]) -> Optional[Path]:
    if r["source_pdf"] and Path(r["source_pdf"]).exists():
        return Path(r["source_pdf"])
    name = Path(str(r["receipt_info"].get("original_file") or r["source_pdf"] or "")).name
    if sources_dir and name and (Path(sources_dir) / name).exists():
        return Path(sources_dir) / name
    return None


def cmd_textcheck(args) -> int:
    receipts = parse_batch(args.xlsx)
    _require_sheets(receipts)
    out = {"clean": [], "findings": {}, "visual": {}}
    for r in receipts:
        src = _resolve_source(r, getattr(args, "sources_dir", None))
        if src is None:
            out["visual"][r["sheet"]] = "source not found"
            continue
        if src.suffix.lower() != ".pdf":
            out["visual"][r["sheet"]] = "image source"
            continue
        text = _pdf_text(src)
        if len(text.strip()) < MIN_TEXT_LAYER_CHARS:
            out["visual"][r["sheet"]] = "no text layer"
            continue
        found = text_findings(r, text)
        if found:
            out["findings"][r["sheet"]] = found
        else:
            out["clean"].append(r["sheet"])
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if out["findings"] else 0


def _is_blank_str(value) -> bool:
    return isinstance(value, str) and not value.strip()


def _validate_fixes(fixes: list, field_rows: dict) -> None:
    """Reject ambiguous entries before anything is written. JSON null is the one
    way to clear a cell; a missing "value" or an empty string is an error, so a
    half-written entry can never blank a field by accident."""
    if not isinstance(fixes, list):
        raise ValueError("fixes.json must be a JSON list")
    for fix in fixes:
        if not isinstance(fix, dict) or "sheet" not in fix:
            raise ValueError(f"Fix entry needs a sheet: {fix}")
        if fix.get("non_expense") or fix.get("resolved"):
            continue
        if "field" in fix:
            if fix["field"] not in field_rows:
                raise ValueError(f"Unknown field {fix['field']!r}: {fix}")
            if "value" not in fix:
                raise ValueError(f'Field fix has no "value" (use null to clear): {fix}')
            if _is_blank_str(fix["value"]):
                raise ValueError(f'Empty-string value - use null to clear: {fix}')
        elif "line_item" in fix:
            values = fix.get("values")
            if not isinstance(values, dict) or not values:
                raise ValueError(f'Line-item fix needs a non-empty "values" object: {fix}')
            for key, value in values.items():
                if _is_blank_str(value):
                    raise ValueError(f'Empty-string {key!r} - use null to clear: {fix}')
        else:
            raise ValueError(f"Unrecognized fix entry: {fix}")


def _fix_cells(fix: dict, config, field_rows: dict) -> list:
    """(row, column, expected value) for every cell a field/line-item fix writes."""
    if fix.get("non_expense") or fix.get("resolved"):
        return []
    if "field" in fix:
        return [(field_rows[fix["field"]], config.header_value_column, fix["value"])]
    if "line_item" in fix:
        row = config.line_items_start_row + fix["line_item"]
        return [(row, config.get_line_item_column(k), v) for k, v in fix["values"].items()]
    return []


def _field_rows(config) -> dict:
    return {field: config.header_start_row + i
            for i, (_, field) in enumerate(config.get_header_fields())}


def cmd_apply_fixes(args) -> int:
    fixes = json.loads(Path(args.fixes).read_text(encoding="utf-8"))
    config = get_excel_config()
    field_rows = _field_rows(config)
    try:
        _validate_fixes(fixes, field_rows)
    except ValueError as e:
        print(f"Invalid {args.fixes}: {e}", file=sys.stderr)
        return 2
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
        elif fix.get("resolved"):
            # the review is settled: drop the red tab and the generator's
            # review warning (an audit note in that cell is kept)
            ws.sheet_properties.tabColor = None
            note_row = config.header_start_row
            cell = ws.cell(row=note_row, column=notes_col)
            if str(cell.value or "").startswith(REVIEW_WARNING_PREFIX):
                cell.value = None
        else:
            if "field" in fix:
                note_row = field_rows[fix["field"]]
            # assign .value directly: ws.cell(..., value=None) leaves the old
            # value in place, which would make a null (clear) a silent no-op
            for row, col, value in _fix_cells(fix, config, field_rows):
                ws.cell(row=row, column=col).value = value
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


def _fix_landed(actual, expected) -> bool:
    """A cleared fix (null) matches an empty cell; numbers compare numerically."""
    if expected is None:
        return actual is None or _is_blank_str(actual)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual == expected
    if isinstance(expected, (int, float)):
        try:
            return abs(float(actual) - expected) < 1e-9
        except (TypeError, ValueError):
            return False
    return str(actual).strip() == str(expected).strip() if actual is not None else False


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

    checked = 0
    for name in wb.sheetnames:
        if not SHEET_RE.fullmatch(name):
            continue
        checked += 1
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

    if not checked:
        problems["workbook"] = ["no R### receipt sheets found - wrong file or unreadable layout"]

    fixes_path = getattr(args, "fixes", None)
    if fixes_path:
        fixes = json.loads(Path(fixes_path).read_text(encoding="utf-8"))
        field_rows = _field_rows(config)
        for fix in fixes:
            if fix.get("sheet") not in wb.sheetnames:
                problems.setdefault(str(fix.get("sheet")), []).append("fix targets a missing sheet")
                continue
            ws = wb[fix["sheet"]]
            for row, col, expected in _fix_cells(fix, config, field_rows):
                actual = ws.cell(row=row, column=col).value
                if not _fix_landed(actual, expected):
                    problems.setdefault(fix["sheet"], []).append(
                        f"fix not applied at row {row} col {col}: "
                        f"expected {'(empty)' if expected is None else expected!r}, "
                        f"found {actual!r}")

    print(json.dumps(problems, ensure_ascii=False, indent=1))
    return 1 if problems else 0


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

    p = sub.add_parser("agent-prompts", help="emit visual-verification agent prompts")
    p.add_argument("xlsx", type=Path)
    p.add_argument("--chunk", type=int, default=6)
    p.add_argument("--scratch", default=str(Path.home() / "AppData" / "Local" / "Temp"),
                   help="directory agents may write temp page renders to")
    p.add_argument("--sheets", nargs="*", metavar="SHEET",
                   help="only these sheets (e.g. the textcheck 'visual' list)")
    p.set_defaults(func=cmd_agent_prompts)

    p = sub.add_parser("textcheck", help="check extracted values against each source "
                                         "PDF's text layer; lists sheets needing visual audit")
    p.add_argument("xlsx", type=Path)
    p.add_argument("--sources-dir", type=Path,
                   help="fallback folder to find originals by filename")
    p.set_defaults(func=cmd_textcheck)

    p = sub.add_parser("reconcile", help="compare blind agent transcriptions (JSON) "
                                         "against the workbook")
    p.add_argument("xlsx", type=Path)
    p.add_argument("transcriptions", type=Path)
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("apply-fixes", help="apply a fixes.json to the workbook")
    p.add_argument("xlsx", type=Path)
    p.add_argument("fixes", type=Path)
    p.add_argument("--backup-dir", type=Path, required=True)
    p.set_defaults(func=cmd_apply_fixes)

    p = sub.add_parser("verify", help="post-fix integrity check")
    p.add_argument("xlsx", type=Path)
    p.add_argument("--fixes", type=Path,
                   help="also check that every field/line-item fix in this fixes.json "
                        "landed (a null fix must have left the cell empty)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("recurring",
                       help="warn if an expected recurring vendor is missing "
                            "(pass all of the period's batches)")
    p.add_argument("xlsx", type=Path, nargs="+")
    p.set_defaults(func=cmd_recurring)

    p = sub.add_parser("history",
                       help="compare the period's batches with everything already "
                            "imported to iCount (duplicates, drift, vendors gone missing)")
    p.add_argument("xlsx", type=Path, nargs="+")
    p.add_argument("--period", help="reporting period YYYY-MM; imports produced after it "
                                    "ended (this period's own consolidation) are ignored")
    p.add_argument("--past", nargs="*", metavar="DIR",
                   help="dirs to search (recursively) for icount_import_*.xls; default: "
                        "receipts_consolidated/ + past_imports_dirs from CONFIG.personal.yaml")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("merge", help="combine batch workbooks into one, optionally "
                                     "dropping sheets (e.g. a duplicate)")
    p.add_argument("out", type=Path)
    p.add_argument("xlsx", type=Path, nargs="+")
    p.add_argument("--drop", nargs="*", metavar="SHEET", help="sheet names to leave out")
    p.set_defaults(func=cmd_merge)

    args = parser.parse_args()
    xlsx_args = args.xlsx if isinstance(args.xlsx, list) else [args.xlsx]
    for x in xlsx_args:
        if not x.exists():
            print(f"File not found: {x}", file=sys.stderr)
            return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
