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

fixes.json is a JSON list; each entry is one of:
  {"sheet": "R001", "field": "vendor", "value": "X", "note": "optional"}
  {"sheet": "R011", "line_item": 0,
   "values": {"description": "...", "amount_excl_vat": 1.0, "vat": 0.2, "total": 1.2},
   "note": "optional"}
  {"sheet": "R022", "non_expense": true, "note": "required"}

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
