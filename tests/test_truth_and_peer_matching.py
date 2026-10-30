"""Tests for missing truth, evidence multiplicity, and input integrity."""

import csv
from dataclasses import replace

import pytest

from veriledger.audit import CrossLedgerAudit, LedgerAudit, SamplingAudit
from veriledger.disclosure import attested, public
from veriledger.formats import load_economy, save_economy
from veriledger.ledger import Ledger, canonical
from veriledger.lending import lend
from veriledger.merkle import leaf_hash, sha256
from veriledger.metrics import evaluate
from veriledger.misstate import inject
from veriledger.model import CounterRecord, Economy, Party, make_entry

BOUNDS = [(0, 4), (5, 9)]


def example():
    e = make_entry("A", "sale", 1, 100, counterparty="C", doc_id="INV")
    return Economy(
        "F", [e], {"C": Party("C", "customer", True)}, {"C": [CounterRecord("C", "INV", 1, 100, "sale")]}, BOUNDS
    )


def change_csv(path, update):
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        fields, rows = r.fieldnames, list(r)
    update(rows)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_no_truth_cannot_be_scored_or_relabelled_as_clean(tmp_path):
    econ = example()
    econ.entries[0].misstatement = "fictitious"
    econ.entries[0].true_amount = 0
    save_economy(econ, tmp_path / "no_truth", truth=False)
    loaded = load_economy(tmp_path / "no_truth")
    assert not loaded.has_ground_truth and not loaded.copy().has_ground_truth
    for operation in (
        lambda: SamplingAudit().run(loaded),
        lambda: LedgerAudit().run(loaded),
        lambda: CrossLedgerAudit().run(loaded),
        lambda: public(loaded),
        lambda: lend(loaded),
        lambda: inject(loaded, {}),
        lambda: evaluate(loaded, LedgerAudit().run(econ)),
        lambda: save_economy(loaded, tmp_path / "fabricated_truth"),
    ):
        with pytest.raises(ValueError, match="ground truth"):
            operation()
    assert not (tmp_path / "fabricated_truth").exists()


def test_matching_identical_multisets_is_order_independent():
    entries = [
        make_entry("A", "sale", 1, 100, counterparty="C", doc_id="INV"),
        make_entry("B", "sale", 1, 200, counterparty="C", doc_id="INV"),
    ]
    econ = Economy(
        "F",
        entries,
        {"C": Party("C", "customer", True)},
        {"C": [CounterRecord("C", "INV", 1, 200, "sale"), CounterRecord("C", "INV", 1, 100, "sale")]},
        BOUNDS,
    )
    checker = CrossLedgerAudit(ledger_checks=False, residual_sampling=False)
    result = checker.run(econ)
    assert result.confirmed == {"A", "B"}
    daily = attested(econ, checker, report_lag=0)
    assert daily.audit["matched"] == 2


def test_duplicate_evidence_cannot_be_reused():
    entries = [
        make_entry("A", "sale", 1, 100, counterparty="C", doc_id="INV"),
        make_entry("B", "sale", 1, 100, counterparty="C", doc_id="INV"),
    ]
    econ = Economy(
        "F", entries, {"C": Party("C", "customer", True)}, {"C": [CounterRecord("C", "INV", 1, 100, "sale")]}, BOUNDS
    )
    checker = CrossLedgerAudit(ledger_checks=False, residual_sampling=False)
    result = checker.run(econ)
    assert len(result.confirmed) == 1 and len(result.checked - result.confirmed) == 1
    assert attested(econ, checker, report_lag=0).audit["matched"] == 1


def test_later_evidence_can_confirm_a_later_identical_posting():
    entries = [
        make_entry("A", "sale", 2, 100, rec_day=1, counterparty="C", doc_id="INV"),
        make_entry("B", "sale", 2, 100, rec_day=2, counterparty="C", doc_id="INV"),
    ]
    econ = Economy(
        "F",
        entries,
        {"C": Party("C", "customer", True)},
        {"C": [CounterRecord("C", "INV", 2, 100, "sale")]},
        BOUNDS,
    )
    checker = CrossLedgerAudit(ledger_checks=False, residual_sampling=False)
    daily = attested(econ, checker, report_lag=0, attest_lag=0)
    assert daily.audit["matched"] == 1


def test_mirror_owner_cannot_be_misrepresented_by_its_container():
    econ = example()
    econ.mirrors["C"] = [CounterRecord("OTHER", "INV", 1, 100, "sale")]
    checker = CrossLedgerAudit(ledger_checks=False, residual_sampling=False)
    with pytest.raises(ValueError, match="owner"):
        checker.run(econ)
    with pytest.raises(ValueError, match="owner"):
        attested(econ, checker, report_lag=0)


@pytest.mark.parametrize(
    "problem",
    [
        "empty_recorded",
        "unknown_label",
        "unlabelled_difference",
        "duplicate_party",
        "invalid_adoption",
        "bad_mirror",
        "unbalanced",
    ],
)
def test_corrupt_economy_bundle_is_rejected(tmp_path, problem):
    save_economy(example(), tmp_path)
    if problem == "empty_recorded":
        change_csv(tmp_path / "truth.csv", lambda rows: rows[0].update(recorded_amount=""))
    elif problem == "unknown_label":
        change_csv(tmp_path / "truth.csv", lambda rows: rows[0].update(misstatement="typo"))
    elif problem == "unlabelled_difference":
        change_csv(tmp_path / "truth.csv", lambda rows: rows[0].update(true_amount="1"))
    elif problem == "duplicate_party":
        change_csv(tmp_path / "parties.csv", lambda rows: rows.append(dict(rows[0])))
    elif problem == "invalid_adoption":
        change_csv(tmp_path / "parties.csv", lambda rows: rows[0].update(adopts="2"))
    elif problem == "bad_mirror":
        change_csv(tmp_path / "mirrors.csv", lambda rows: rows[0].update(amount="nan"))
    else:
        change_csv(tmp_path / "entries.csv", lambda rows: rows[1].update(credit="0"))
    with pytest.raises(ValueError):
        load_economy(tmp_path)


def test_ledger_verification_checks_record_structure():
    led = Ledger.from_entries("F", [make_entry("A", "sale", 1, 100)], BOUNDS)
    led.records[0]["lines"][1][2] = 0
    leaf = leaf_hash(canonical(led.records[0]))
    chain = sha256(led.genesis + leaf)
    led.leaves[0], led.chain[0] = leaf, chain
    led.commits[0] = replace(led.commits[0], root=leaf, chain_head=chain)
    led.commits[1] = replace(led.commits[1], chain_head=chain)
    report = led.verify(dict(led.commits))
    assert not report.ok
    assert any("record" in error.lower() for error in report.errors)
