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
        make_result(number="10", date="2022-07-12"),      # out of period
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
