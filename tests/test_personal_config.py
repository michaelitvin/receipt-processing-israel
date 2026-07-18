import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from shared import personal_config as pc


def _write(tmp_path, monkeypatch, text):
    f = tmp_path / "config.personal.yaml"
    f.write_text(text, encoding="utf-8")
    monkeypatch.setattr(pc, "PERSONAL_CONFIG_FILE", f)
    return f


def test_missing_file_yields_empty_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "PERSONAL_CONFIG_FILE", tmp_path / "nope.yaml")
    assert pc.load_personal_config() == {}
    assert pc.get_own_tax_ids() == set()
    assert pc.get_income_tax_advance_rate() is None


def test_reads_and_normalizes_own_ids(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           "own_tax_ids:\n  - \"123456782\"\n  - \"12345678\"\n")
    # zero-padded to 9 digits, hyphens/spaces irrelevant
    assert pc.get_own_tax_ids() == {"123456782", "012345678"}


def test_own_ids_accepts_comma_string(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, 'own_tax_ids: "123456782, 12345678"\n')
    assert pc.get_own_tax_ids() == {"123456782", "012345678"}


def test_reads_advance_rate(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "income_tax_advance_rate: 10\n")
    assert pc.get_income_tax_advance_rate() == 10.0


def test_partial_config_leaves_other_default(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "income_tax_advance_rate: 7\n")
    assert pc.get_income_tax_advance_rate() == 7.0
    assert pc.get_own_tax_ids() == set()
