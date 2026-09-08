import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

import vat_report as v

# Header sets matching real iCount exports.
EXPENSE_HEADERS = [
    "סוג הוצאה", "תיאור", "סטאטוס", "ספק", "מס. מסמך", "תאריך ערך",
    "תא' אסמכתא", "תא' תשלום", "שייך לתא' דיווח", "סכום₪", "סכום",
    "ניכוי מס במקור", "מע\"מ מוכר", "הוצאה מוכרת", "קישור",
]
INCOME_HEADERS = [
    "לקוח / ספק", "מספר מסמך", "תאריך", "סוג הכנסה", "סה\"כ לפני מע\"מ",
    "מע\"מ", "סה\"כ כולל מע\"מ", "יתרה", "ILS", "פרטים נוספים",
]


# ---- resolve_columns: name-based, order-independent ----

def test_resolve_columns_by_name_not_position():
    cols = v.resolve_columns(EXPENSE_HEADERS, "expenses")
    assert cols == {"date": EXPENSE_HEADERS.index("תא' אסמכתא"), "expense_type": 0, "recognized_vat": 12}


def test_resolve_columns_adapts_to_reorder():
    reordered = list(reversed(EXPENSE_HEADERS))
    cols = v.resolve_columns(reordered, "expenses")
    # indices differ, but they point at the right headers
    assert reordered[cols["recognized_vat"]] == "מע\"מ מוכר"
    assert reordered[cols["expense_type"]] == "סוג הוצאה"


def test_resolve_columns_missing_computed_raises():
    headers = [h for h in EXPENSE_HEADERS if h != "מע\"מ מוכר"]
    with pytest.raises(ValueError, match="מע\"מ מוכר"):
        v.resolve_columns(headers, "expenses")


# ---- require_columns: adapt to reorder, fail loudly on rename/drop ----

def test_require_columns_passes_current_exports():
    v.require_columns(EXPENSE_HEADERS, "expenses")
    v.require_columns(INCOME_HEADERS, "income")


def test_require_columns_reorder_ok():
    v.require_columns(list(reversed(EXPENSE_HEADERS)), "expenses")


def test_require_columns_fails_on_renamed_core_column():
    renamed = ["סכום" if h == "סכום₪" else h for h in EXPENSE_HEADERS]
    with pytest.raises(ValueError, match="סכום₪"):
        v.require_columns(renamed, "expenses")


def test_require_columns_fails_on_dropped_link():
    dropped = [h for h in EXPENSE_HEADERS if h != "קישור"]
    with pytest.raises(ValueError, match="קישור"):
        v.require_columns(dropped, "expenses")


def test_require_columns_allows_missing_optional_column():
    # ניכוי מס במקור is conditional (absent Jan-Feb 2026) - must NOT fail.
    without_optional = [h for h in EXPENSE_HEADERS if h != "ניכוי מס במקור"]
    v.require_columns(without_optional, "expenses")


def test_require_columns_names_all_missing_at_once():
    stripped = [h for h in EXPENSE_HEADERS if h not in ("ספק", "קישור")]
    with pytest.raises(ValueError) as exc:
        v.require_columns(stripped, "expenses")
    assert "ספק" in str(exc.value) and "קישור" in str(exc.value)


# ---- detect_file_type: name-based ----

def test_detect_file_type_by_headers(tmp_path):
    import openpyxl
    for headers, kind in [(EXPENSE_HEADERS, "expenses"), (INCOME_HEADERS, "income")]:
        wb = openpyxl.Workbook()
        wb.active.append(headers)
        p = tmp_path / f"{kind}.xlsx"
        wb.save(p)
        assert v.detect_file_type(str(p)) == kind


def test_expenses_date_survives_icount_relabel_of_neighbour_column():
    """2026-09 export: iCount renamed the column next to the reference date
    ("תאריך ערך" -> "לתשלום עד"). The report keys the expenses date on
    "תא' אסמכתא", which is unchanged, so the new export must still load."""
    sep_2026 = ["לתשלום עד" if h == "תאריך ערך" else h for h in EXPENSE_HEADERS]
    v.require_columns(sep_2026, "expenses")
    cols = v.resolve_columns(sep_2026, "expenses")
    assert sep_2026[cols["date"]] == "תא' אסמכתא"
    assert v.resolve_columns(EXPENSE_HEADERS, "expenses")["date"] == EXPENSE_HEADERS.index("תא' אסמכתא")


# ---- numeric coercion: text-typed amount cells must not break the Excel formulas ----

def _write_income(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(INCOME_HEADERS)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _income_row(doc, date, before, vat):
    return ["ACME", doc, date, None, before, vat, None, None, None, None]


def test_load_income_coerces_text_amounts_to_numbers(tmp_path):
    """2026-09 cycle: a pre-filtered export (pandas dtype=str) carried '1281.07' and '0'
    as text. Excel ranks any text above any number, so "0">0 was TRUE and the exempt
    invoice landed in עסקאות חייבות. The loader must hand the sheet real numbers."""
    p = tmp_path / "income.xlsx"
    _write_income(p, [_income_row("חשק. 3016", "14/07/2026", "1281.07", "0")])
    data = v.load_income(str(p))
    cells = data["rows"][0]["cells"]
    assert cells[data["cols"]["before_vat"]] == 1281.07
    assert cells[data["cols"]["vat"]] == 0
    assert isinstance(cells[data["cols"]["vat"]], float)


def test_load_income_leaves_non_numeric_text_alone(tmp_path):
    p = tmp_path / "income.xlsx"
    _write_income(p, [_income_row("חשק. 3016", "14/07/2026", "n/a", None)])
    data = v.load_income(str(p))
    cells = data["rows"][0]["cells"]
    assert cells[data["cols"]["before_vat"]] == "n/a"
    assert cells[data["cols"]["vat"]] is None
    assert cells[1] == "חשק. 3016"  # non-amount columns untouched


def test_load_expenses_coerces_text_recognized_vat(tmp_path):
    import openpyxl
    from datetime import datetime
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(EXPENSE_HEADERS)
    row = [None] * len(EXPENSE_HEADERS)
    row[EXPENSE_HEADERS.index("תא' אסמכתא")] = datetime(2026, 7, 4)
    row[EXPENSE_HEADERS.index("סוג הוצאה")] = "סלולר"
    row[EXPENSE_HEADERS.index("מע\"מ מוכר")] = "12.5"
    ws.append(row)
    p = tmp_path / "exp.xlsx"
    wb.save(p)
    data = v.load_expenses(str(p))
    assert data["rows"][0]["cells"][data["cols"]["recognized_vat"]] == 12.5


# ---- --period: restrict the report to one bi-monthly period ----

def test_parse_report_period():
    assert v.parse_report_period("2026-07") == (2026, 7, 8)
    assert v.parse_report_period("2026-08") == (2026, 7, 8)
    assert v.parse_report_period("2025-12") == (2025, 11, 12)


def test_parse_report_period_rejects_garbage():
    with pytest.raises(ValueError):
        v.parse_report_period("July 2026")
    with pytest.raises(ValueError):
        v.parse_report_period("2026-13")


def test_select_period_keeps_only_that_bimonthly_period():
    from datetime import datetime
    rows = [
        {"date": datetime(2026, 6, 30), "cells": [], "formats": []},
        {"date": datetime(2026, 7, 14), "cells": [], "formats": []},
        {"date": datetime(2026, 8, 31), "cells": [], "formats": []},
        {"date": datetime(2026, 9, 1), "cells": [], "formats": []},
    ]
    kept = v.select_period(rows, (2026, 7, 8))
    assert [r["date"].month for r in kept] == [7, 8]
