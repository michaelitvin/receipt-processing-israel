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
    items = [{"total": 40.00}, {"total": 40.00}]
    assert check_receipt(make_receipt(net=67.80, vat=9.2, total=80.00, line_items=items)) == []


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
