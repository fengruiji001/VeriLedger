"""Tests for decision timing, numerical bounds and external evidence."""

import json

import pytest

from veriledger.audit import CrossLedgerAudit, DetectionModel, poisson_upper
from veriledger.cli import main
from veriledger.disclosure import attested
from veriledger.formats import read_saft, write_saft
from veriledger.ledger import Ledger
from veriledger.model import CounterRecord, Economy, Party, make_entry
from veriledger.scenario import Scenario

BOUNDS = [(0, 89), (90, 180), (181, 272), (273, 364)]


def test_unresolved_tampering_uses_current_book_amount():
    e = make_entry("T", "sale", 5, 100, counterparty="C", doc_id="INV")
    e.misstatement = "tamper"
    econ = Economy(
        "F",
        [e],
        {"C": Party("C", "customer", True)},
        {"C": [CounterRecord("C", "INV", 5, 100, "sale")]},
        BOUNDS,
        tampering={"T": 10000},
        tamper_days={"T": 95},
    )
    result = CrossLedgerAudit().run(econ, detection=DetectionModel(investigate={"tamper": 0}))
    assert result.unresolved == {"T"}
    assert result.upper_limit == 10000 and result.opinion == "modified"


def test_future_counterparty_record_does_not_change_previous_publication():
    e = make_entry("A", "sale", 80, 200, rec_day=10, counterparty="C", doc_id="INV")
    e.misstatement, e.true_amount = "inflate", 100

    def outcome(records):
        econ = Economy("F", [e.copy()], {"C": Party("C", "customer", True)}, {"C": records}, BOUNDS)
        checker = CrossLedgerAudit(ledger_checks=False, residual_sampling=False)
        return attested(econ, audit=checker, report_lag=0).outsider[11]

    assert outcome([]) == 100
    assert outcome([CounterRecord("C", "INV", 80, 200, "sale")]) == 100


def test_available_confirmation_changes_that_days_attestation():
    e = make_entry("A", "sale", 10, 200, counterparty="C", doc_id="INV")
    e.misstatement, e.true_amount = "inflate", 100
    econ = Economy(
        "F", [e], {"C": Party("C", "customer", True)}, {"C": [CounterRecord("C", "INV", 10, 200, "sale")]}, BOUNDS
    )
    result = attested(econ, report_lag=0)
    assert result.outsider[11] == 200 and result.audit["matched"] == 1


def test_daily_attestation_does_not_claim_year_end_residual_sampling():
    with pytest.raises(ValueError, match="residual_sampling=False"):
        Scenario.from_dict(
            {"experiment": "disclosure", "regime_params": {"attested": {"audit": {"residual_sampling": True}}}}
        )


def test_each_replicated_chain_link_is_checked():
    led = Ledger.from_entries("F", [make_entry("A", "sale", 1, 10), make_entry("B", "sale", 2, 20)], BOUNDS)
    trusted = dict(led.commits)
    led.chain[0] = b"\xff" * 32
    assert not led.verify(trusted).ok and not led.verify(trusted).chain_ok


def test_altered_late_flag_cannot_pass_with_trusted_commitments(tmp_path):
    bounds = [(0, 4), (5, 9)]
    led = Ledger.from_entries(
        "F", [make_entry("A", "sale", 0, 100), make_entry("B", "sale", 1, 100, rec_day=6)], bounds, close_lag=0
    )
    path = tmp_path / "ledger.jsonl"
    led.save(path)
    rows = path.read_text().splitlines()
    row = json.loads(rows[2])
    row["late"] = False
    rows[2] = json.dumps(row)
    path.write_text("\n".join(rows) + "\n")
    result = Ledger.load(path).verify(led.commits)
    assert not result.ok and any("late-record" in error for error in result.errors)


def test_duplicate_record_ids_in_ledger_file_are_rejected(tmp_path):
    path = tmp_path / "ledger.jsonl"
    led = Ledger.from_entries("F", [make_entry("A", "sale", 1, 10), make_entry("B", "sale", 2, 20)], BOUNDS)
    led.save(path)
    rows = path.read_text().splitlines()
    row = json.loads(rows[2])
    row["record"]["entry_id"] = "A"
    rows[2] = json.dumps(row)
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(ValueError, match="duplicate entry_id"):
        Ledger.load(path)
    assert main(["verify", str(path)]) == 2


def test_malformed_commitment_returns_input_error(tmp_path):
    path = tmp_path / "ledger.jsonl"
    Ledger.from_entries("F", [make_entry("A", "sale", 1, 10)], BOUNDS).save(path)
    c = tmp_path / "bad.json"
    c.write_text(
        json.dumps([{"period": 0, "root": None, "size": 1, "commit_day": 94, "chain_head": "00", "n_records": 1}])
    )
    assert main(["verify", str(path), "--commits", str(c)]) == 2


@pytest.mark.parametrize("k,expected", [(750, 796.6375067429809), (1000, 1053.6031221333008)])
def test_large_poisson_bounds_do_not_underflow(k, expected):
    assert poisson_upper(k) == pytest.approx(expected, abs=1e-7)


@pytest.mark.parametrize("k,confidence", [(-1, 0.95), (1.5, 0.95), (1, 1), (1, 0)])
def test_poisson_input_validation(k, confidence):
    with pytest.raises(ValueError):
        poisson_upper(k, confidence)


def test_saft_generated_suffix_cannot_collide_with_source_id(tmp_path):
    entries = [make_entry("A", "sale", 1, 10), make_entry("A", "sale", 2, 10), make_entry("A#2", "sale", 3, 10)]
    path = tmp_path / "x.xml"
    write_saft(entries, path)
    ids = [e.entry_id for e in read_saft(path)]
    assert len(ids) == len(set(ids)) == 3


def test_publication_of_pre_year_posting_does_not_index_from_array_end():
    e = make_entry("A", "sale", 1, 100, rec_day=-10, counterparty="C", doc_id="I")
    econ = Economy(
        "F", [e], {"C": Party("C", "customer", True)}, {"C": [CounterRecord("C", "I", 1, 100, "sale")]}, BOUNDS
    )
    assert attested(econ, report_lag=0).outsider[0] == 100
