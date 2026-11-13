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
  {"sheet": "R011", "line_item": 0,
   "values": {"description": "...", "amount_excl_vat": 1.0, "vat": 0.2, "total": 1.2},
   "note": "optional"}
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
    _require_sheets(receipts)
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
        elif fix.get("resolved"):
            # the review is settled: drop the red tab and the generator's
            # review warning (an audit note in that cell is kept)
            ws.sheet_properties.tabColor = None
            note_row = config.header_start_row
            cell = ws.cell(row=note_row, column=notes_col)
            if str(cell.value or "").startswith(REVIEW_WARNING_PREFIX):
                cell.value = None
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
    p.set_defaults(func=cmd_agent_prompts)

    p = sub.add_parser("apply-fixes", help="apply a fixes.json to the workbook")
    p.add_argument("xlsx", type=Path)
    p.add_argument("fixes", type=Path)
    p.add_argument("--backup-dir", type=Path, required=True)
    p.set_defaults(func=cmd_apply_fixes)

    p = sub.add_parser("verify", help="post-fix integrity check")
    p.add_argument("xlsx", type=Path)
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
