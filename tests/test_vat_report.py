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
    assert cols == {"date": 5, "expense_type": 0, "recognized_vat": 12}


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
