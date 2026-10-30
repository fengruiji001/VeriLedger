"""Tests for serialization boundaries and amount-edit consistency."""

import copy
import json

import pytest

from veriledger.audit import build_ledger
from veriledger.formats import GL_COLUMNS, load_economy, read_gl_csv, save_economy
from veriledger.ledger import Ledger
from veriledger.model import Economy, Entry, Line, make_entry, with_amount

BOUNDS = [(0, 4), (5, 9)]


def test_zero_amount_ledger_tamper_preserves_standard_postings(tmp_path):
    entry = make_entry("Z", "sale", 1, 0)
    entry.misstatement = "tamper"
    econ = Economy("F", [entry], {}, {}, BOUNDS, tampering={"Z": 100}, tamper_days={"Z": 7})
    led = build_ledger(econ)
    record = led.records[0]
    assert record["lines"] == [["1500", 100.0, 0.0], ["3000", 0.0, 100.0]]
    report = led.verify()
    assert report.modified == [0] and not report.errors
    led.save(tmp_path / "ledger.jsonl")
    assert Ledger.load(tmp_path / "ledger.jsonl").records == led.records


@pytest.mark.parametrize("amount", ["invalid", float("nan"), -1, True])
def test_rejected_tamper_is_atomic(amount):
    led = Ledger.from_entries("F", [make_entry("A", "sale", 1, 100)], BOUNDS)
    before = copy.deepcopy(led.records)
    with pytest.raises(ValueError):
        led.tamper("A", amount=amount, description="should not be written")
    assert led.records == before and led.verify().ok


def test_ambiguous_amount_edit_is_rejected_before_any_mutation():
    entry = Entry("A", "other", 1, 1, [Line("X"), Line("Y")], 0)
    led = Ledger.from_entries("F", [entry], BOUNDS)
    before = copy.deepcopy(led.records)
    with pytest.raises(ValueError, match="infer"):
        led.tamper("A", amount=1)
    assert led.records == before


@pytest.mark.parametrize(
    "field,value", [("amount", float("nan")), ("eff_day", 1.5), ("rec_day", True), ("entry_id", ""), ("doc_id", 12)]
)
def test_mutated_entries_are_revalidated_before_append(field, value):
    entry = make_entry("A", "sale", 1, 100)
    setattr(entry, field, value)
    led = Ledger("F", BOUNDS)
    with pytest.raises(ValueError):
        led.append(entry)
    assert not led.records and not led.commits


@pytest.mark.parametrize("truth", [True, False])
def test_empty_economy_bundle_roundtrip(tmp_path, truth):
    save_economy(Economy("F", [], {}, {}, BOUNDS, last_day=9), tmp_path, truth=truth)
    loaded = load_economy(tmp_path)
    assert loaded.entries == [] and loaded.has_ground_truth is truth
    assert loaded.last_day == 9


@pytest.mark.parametrize(
    "columns,mapping",
    [
        (["entry_id", "date", "account", "debit", "credit", "debit"], None),
        (["entry_id", "date", "account", "debit", "credit", "Dr"], {"Dr": "debit"}),
    ],
)
def test_csv_duplicate_or_colliding_columns_are_rejected(tmp_path, columns, mapping):
    path = tmp_path / "file.csv"
    path.write_text(",".join(columns) + "\nA,2025-01-02,1500,100,0,0\nA,2025-01-02,3000,0,100,0\n")
    with pytest.raises(ValueError, match="column"):
        read_gl_csv(path, mapping=mapping)


@pytest.mark.parametrize("row", ["A,2025-01-02,1500,0", "A,2025-01-02,1500,0,0,extra"])
def test_csv_ragged_rows_are_rejected(tmp_path, row):
    path = tmp_path / "file.csv"
    path.write_text("entry_id,date,account,debit,credit\n" + row + "\n")
    with pytest.raises(ValueError, match="column"):
        read_gl_csv(path)


def test_empty_csv_checks_its_header(tmp_path):
    path = tmp_path / "file.csv"
    path.write_text(",".join(GL_COLUMNS) + "\n")
    assert read_gl_csv(path) == []
    path.write_text("entry_id,date\n")
    with pytest.raises(ValueError, match="missing columns"):
        read_gl_csv(path)


@pytest.mark.parametrize(
    "field,value", [("last_day", 9.5), ("last_day", True), ("tamper_days", {"UNKNOWN": 7}), ("tamper_days", {"A": "7"})]
)
def test_economy_event_metadata_is_validated(tmp_path, field, value):
    save_economy(Economy("F", [make_entry("A", "sale", 1, 100)], {}, {}, BOUNDS), tmp_path)
    path = tmp_path / "economy.json"
    meta = json.loads(path.read_text())
    meta[field] = value
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        load_economy(tmp_path)


@pytest.mark.parametrize("amount", [True, -0.001])
def test_amount_helpers_validate_before_conversion_or_rounding(amount):
    with pytest.raises(ValueError):
        make_entry("A", "sale", 1, amount)
    with pytest.raises(ValueError):
        with_amount(make_entry("A", "sale", 1, 100), amount)
