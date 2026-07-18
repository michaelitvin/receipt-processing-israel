# Copyright (c) 2025 Michael Litvin
# Licensed under AGPL-3.0-or-later - see LICENSE file for details
"""Loader for config.personal.yaml - the business's non-secret personal config.

Secrets (API keys) stay in .env, which is ignored by BOTH repos and never backed
up. This file holds personal-but-not-secret settings (own tax ids, income-tax
advance rate); it is ignored by the public repo but tracked and backed up by the
.git-personal overlay (see docs/PERSONAL_BACKUP.md), so the values survive a
machine loss. Absent file -> empty config (the settings are all optional).
"""
from pathlib import Path
from typing import Any, Dict, Optional, Set

import yaml

from shared.receipt_checks import parse_own_ids

PERSONAL_CONFIG_FILE = Path(__file__).parent.parent / "config.personal.yaml"


def load_personal_config() -> Dict[str, Any]:
    """Parsed config.personal.yaml, or {} if it is absent."""
    if not PERSONAL_CONFIG_FILE.exists():
        return {}
    return yaml.safe_load(PERSONAL_CONFIG_FILE.read_text(encoding="utf-8")) or {}


def get_own_tax_ids(config: Optional[Dict[str, Any]] = None) -> Set[str]:
    """The business's own normalized tax ids (owner + spouse + company ח.פ).

    Accepts a YAML list or a comma/semicolon-separated string; both normalize the
    same way (hyphens/spaces and a dropped leading zero don't matter).
    """
    if config is None:
        config = load_personal_config()
    ids = config.get("own_tax_ids") or []
    if isinstance(ids, str):
        return parse_own_ids(ids)
    return parse_own_ids(",".join(str(i) for i in ids))


def get_income_tax_advance_rate(config: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Income-tax advance rate in percent (e.g. 12.0), or None if unset."""
    if config is None:
        config = load_personal_config()
    rate = config.get("income_tax_advance_rate")
    return float(rate) if rate is not None else None
