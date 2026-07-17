import pytest

from shared.receipt_checks import (parse_period, check_receipt, check_batch,
                                   valid_israeli_id, parse_own_ids, normalize_id,
                                   missing_recurring_vendors)


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


# ---- Israeli id check digit ----

# synthetic 9-digit ids (valid check digit) and separator variant - no real entity
VALID_IDS = ["123456782", "222222226", "100000009", "202020202", "13570000-3"]
# same numbers with the check digit bumped by one -> invalid (stands in for OCR slips)
INVALID_IDS = ["123456783", "222222227", "100000000", "202020203"]


def test_valid_israeli_id_accepts_valid_check_digits():
    for good in VALID_IDS:
        assert valid_israeli_id(good), good


def test_valid_israeli_id_rejects_bad_check_digits():
    for bad in INVALID_IDS:
        assert not valid_israeli_id(bad), bad


def test_valid_israeli_id_rejects_foreign_and_junk():
    for bad in ["IE 1234567 X", "NA", "", "1234567890"]:
        assert not valid_israeli_id(bad)


def test_check_receipt_flags_bad_check_digit():
    warnings = check_receipt(make_receipt(vendor_id="123456783"))
    assert any("ספרת ביקורת שגויה" in w for w in warnings)


def test_check_receipt_accepts_good_check_digit():
    warnings = check_receipt(make_receipt(vendor_id="123456782"))
    assert not any("ספרת ביקורת" in w for w in warnings)


def test_check_receipt_skips_foreign_vendor_id_format():
    # foreign id must not trip the check-digit rule (only the missing-id rule applies to blanks)
    warnings = check_receipt(make_receipt(vendor_id="IE 1234567 X"))
    assert not any("ספרת ביקורת" in w for w in warnings)


def test_check_receipt_skips_check_digit_for_foreign_currency():
    # a US-EIN-shaped id (9 digits, hyphen after 2nd) on a USD receipt must not be
    # validated as Israeli even though it fails the Israeli check digit
    warnings = check_receipt(make_receipt(vendor_id="12-3456789", currency="USD"))
    assert not any("ספרת ביקורת" in w for w in warnings)


# ---- own-id guard ----

def test_parse_own_ids_splits_and_normalizes():
    # separators vary; a dropped leading zero must still match
    assert parse_own_ids("123456782, 22222222-6; 90000000") == {
        normalize_id("123456782"), normalize_id("222222226"), normalize_id("090000000")}
    assert parse_own_ids("") == set()
    assert parse_own_ids(None) == set()


def test_check_receipt_flags_own_id_as_vendor():
    own = parse_own_ids("123456782")
    warnings = check_receipt(make_receipt(vendor_id="123456782"), own_ids=own)
    assert any("של העסק/הבעלים" in w for w in warnings)


def test_own_id_flagged_even_with_valid_check_digit_and_formatting():
    # own id is a *valid* Israeli id, so only the own-id list can catch it;
    # matching ignores hyphens and a dropped leading zero
    own = parse_own_ids("012345678")  # synthetic, valid check digit unnecessary here
    r = make_receipt(vendor_id="12345678")  # leading zero dropped
    assert any("של העסק/הבעלים" in w for w in check_receipt(r, own_ids=own))


def test_check_receipt_does_not_flag_normal_vendor_id():
    own = parse_own_ids("123456782")
    warnings = check_receipt(make_receipt(vendor_id="222222226"), own_ids=own)
    assert not any("של העסק/הבעלים" in w for w in warnings)


def test_check_batch_threads_own_ids():
    own = parse_own_ids("123456782")
    receipts = [make_receipt(number="1", vendor_id="123456782"),
                make_receipt(number="2", vendor_id="222222226", date="2026-06-07")]
    result = check_batch(receipts, own_ids=own)
    assert any("של העסק/הבעלים" in w for w in result[0])
    assert not any("של העסק/הבעלים" in w for w in result[1])


# ---- missing recurring vendors ----

RECURRING = [
    {"name": "Mobile", "keywords": ["אקמי", "AcmeMobile"]},
    {"name": "Internet", "keywords": ["נטקום", "NetCom"]},
    {"name": "Water", "keywords": ["מים לעיר"]},
]


def test_missing_recurring_vendors_reports_absent_only():
    receipts = [make_receipt(vendor="AcmeMobile (אקמי)"),
                make_receipt(vendor='מים לעיר בע"מ')]
    missing = missing_recurring_vendors(receipts, RECURRING)
    assert missing == ["Internet"]  # NetCom absent; Mobile + Water present


def test_missing_recurring_vendors_substring_and_case_insensitive():
    receipts = [make_receipt(vendor='NETCOM ISRAEL LTD')]
    missing = missing_recurring_vendors(receipts, [RECURRING[1]])
    assert missing == []


def test_missing_recurring_vendors_matches_by_id():
    # present by id even though the name doesn't match any keyword
    spec = [{"name": "Mobile", "ids": ["123456782"], "keywords": ["אקמי"]}]
    receipts = [make_receipt(vendor="unrecognized name", vendor_id="123456782")]
    assert missing_recurring_vendors(receipts, spec) == []


def test_missing_recurring_vendors_id_match_ignores_formatting():
    spec = [{"name": "Vendor", "ids": ["012345678"]}]
    receipts = [make_receipt(vendor="x", vendor_id="12345678")]  # dropped leading zero
    assert missing_recurring_vendors(receipts, spec) == []


def test_missing_recurring_vendors_id_or_name_both_absent():
    spec = [{"name": "Vendor", "ids": ["123456782"], "keywords": ["אקמי"]}]
    receipts = [make_receipt(vendor="something else", vendor_id="111111111")]
    assert missing_recurring_vendors(receipts, spec) == ["Vendor"]


def test_missing_recurring_vendors_all_present():
    receipts = [make_receipt(vendor="אקמי"), make_receipt(vendor="נטקום"),
                make_receipt(vendor="מים לעיר")]
    assert missing_recurring_vendors(receipts, RECURRING) == []


def test_missing_recurring_vendors_empty_spec():
    assert missing_recurring_vendors([make_receipt()], []) == []


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
