# Copyright (c) 2025 Michael Litvin
# Licensed under AGPL-3.0-or-later - see LICENSE file for details
"""Structural sanity checks shared by the extractor and the audit tool.

Pure functions over plain receipt dicts shaped like extractor results:
    {'receipt_info': {...}, 'amounts': {...}, 'line_items': [...]}
Checks that need the source document (visual verification) do NOT belong
here - they are the audit's job, and their value is independence.
"""
import re
from typing import Any, Dict, List, Optional, Set

VALID_CURRENCIES = {'ILS', 'USD', 'EUR'}
AMOUNT_TOLERANCE = 0.02   # receipts themselves round by up to one agora
VAT_RATE = 0.18
VAT_RATE_TOLERANCE = 0.005


def normalize_id(value: Any) -> str:
    """Digits-only, zero-padded to 9, for comparing ids that may differ in
    formatting (hyphens, spaces) or a dropped leading zero. Non-Israeli-length
    ids are returned digits-only without padding."""
    digits = ''.join(ch for ch in str(value) if ch.isdigit())
    return digits.zfill(9) if 0 < len(digits) <= 9 else digits


def parse_own_ids(text: Optional[str]) -> Set[str]:
    """Parse a comma/semicolon/whitespace-separated list of the business's own
    tax ids (owner + spouse, etc.) into a set of normalized ids. Empty if None."""
    if not text:
        return set()
    return {normalize_id(part) for part in re.split(r'[,;\s]+', text.strip()) if part}


def valid_israeli_id(value: Any) -> bool:
    """True if value is a valid 9-digit Israeli ID / ח.פ (Luhn-style check digit).

    Foreign vendor ids (e.g. 'IE 8256796 U', 'NA', '') are not 9 pure digits and
    return False here; callers must only treat that as an error when an Israeli id
    is actually expected. Israeli ids shorter than 9 digits are zero-padded.
    """
    digits = ''.join(ch for ch in str(value) if ch.isdigit())
    if not digits or len(digits) > 9:
        return False
    digits = digits.zfill(9)
    total = 0
    for i, ch in enumerate(digits):
        d = int(ch) * (1 if i % 2 == 0 else 2)
        total += d if d < 10 else d - 9
    return total % 10 == 0


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
                  period_months: Optional[List[str]] = None,
                  own_ids: Optional[Set[str]] = None) -> List[str]:
    """Return warnings for a single receipt (empty list = clean).

    own_ids: normalized ids of the business/owner/spouse. A vendor_id equal to one
    of these is the extractor mistaking our own id for the vendor's - always wrong.
    """
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
    vendor_id = info.get('vendor_id')
    own = {normalize_id(x) for x in own_ids} if own_ids else set()
    if not vendor_id:
        warnings.append('חסר תז/חפ הספק')
    elif own and normalize_id(vendor_id) in own:
        warnings.append(f'תז/חפ הספק זהה לזה של העסק/הבעלים - כנראה שגוי: {vendor_id}')
    elif (info.get('currency') or 'ILS') == 'ILS':
        # Only validate ids on domestic (ILS) receipts, where an Israeli ח.פ is
        # expected. Foreign vendors carry foreign-format ids that happen to be 9
        # digits (e.g. a US EIN like NN-NNNNNNN) and must not trip the check digit.
        stripped = ''.join(ch for ch in str(vendor_id) if ch not in ' .-/')
        if stripped.isdigit() and 8 <= len(stripped) <= 9 and not valid_israeli_id(stripped):
            warnings.append(f'ספרת ביקורת שגויה בתז/חפ הספק: {vendor_id}')

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
                period_months: Optional[List[str]] = None,
                own_ids: Optional[Set[str]] = None) -> Dict[int, List[str]]:
    """Per-receipt warnings plus cross-receipt duplicate detection"""
    own = {normalize_id(x) for x in own_ids} if own_ids else None
    result = {i: check_receipt(r, period_months, own) for i, r in enumerate(receipts)}

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


def missing_recurring_vendors(receipts: List[Dict[str, Any]],
                              recurring: List[Dict[str, Any]]) -> List[str]:
    """Names of expected recurring vendors absent from these receipts.

    recurring: [{"name": str, "keywords": [str, ...]}, ...] - a vendor is present
    if any of its keywords is a case-insensitive substring of any receipt's vendor
    name. Pass every batch of the period together so a vendor in another batch is
    not falsely reported missing.
    """
    vendors_low = [str(r.get('receipt_info', {}).get('vendor', '')).lower()
                   for r in receipts]
    missing = []
    for entry in recurring or []:
        name = entry.get('name') or ''
        keywords = entry.get('keywords') or []
        present = any(kw.lower() in v for kw in keywords for v in vendors_low)
        if not present:
            missing.append(name)
    return missing


def _num(value: Any) -> float:
    """Lenient numeric coercion - xlsx round-trips may yield strings"""
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(',', ''))
    except ValueError:
        return 0.0
