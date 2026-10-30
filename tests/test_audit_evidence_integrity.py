"""Tests for audit evidence, decision outcomes and ledger integrity."""

import json
from dataclasses import replace

import pytest

from veriledger.audit import CrossLedgerAudit, DetectionModel, SamplingAudit
from veriledger.cli import main
from veriledger.disclosure import periodic
from veriledger.formats import read_gl_csv, save_economy
from veriledger.ledger import Ledger
from veriledger.metrics import evaluate
from veriledger.model import CounterRecord, Economy, Entry, Line, Party, make_entry
from veriledger.scenario import Scenario

BOUNDS = [(0, 89), (90, 180), (181, 272), (273, 364)]


def economy(entries, mirrors=None):
    return Economy(
        "F",
        entries,
        {"C": Party("C", "customer", True), "S": Party("S", "supplier", True)},
        mirrors or {},
        BOUNDS,
        last_day=394,
    )


def fictitious(kind="sale"):
    e = make_entry("F", kind, 5, 1000, counterparty="C" if kind == "sale" else "S", doc_id="INV")
    e.true_amount, e.misstatement = 0.0, "fictitious"
    return e


@pytest.mark.parametrize("correct", [False, True])
def test_duplicate_procedure_precedes_opinion_and_only_corrections_reduce_residual(correct):
    a = make_entry("A", "sale", 5, 1000, counterparty="C", doc_id="INV")
    b = a.copy(entry_id="B", rec_day=6, true_amount=0.0, misstatement="duplicate")
    e = economy([a, b])
    r = SamplingAudit(correct=correct, duplicate_test=True).run(
        e, detection=DetectionModel(vouch={"duplicate": 0.0}, investigate={"duplicate": 1.0})
    )
    m = evaluate(e, r)
    assert r.detected == {"B"}
    assert r.known_misstatement == 1000
    assert m["residual_overstatement_annual"] == (0 if correct else 1000)
    assert r.opinion == ("clean" if correct else "modified")


def test_crossledger_checked_is_not_successfully_matched_and_known_errors_count():
    e = economy([fictitious()])
    r = CrossLedgerAudit(correct=False).run(e)
    assert r.checked == {"F"} and r.matched == 0 and r.confirmed == set()
    assert r.known_misstatement == 1000 and r.upper_limit == 1000
    assert r.opinion == "modified" and evaluate(e, r)["residual_overstatement_annual"] == 1000


def test_unresolved_matching_exception_cannot_receive_zero_upper_limit():
    e = economy([fictitious()])
    r = CrossLedgerAudit().run(e, detection=DetectionModel(investigate={"fictitious": 0.0}))
    assert r.unresolved == {"F"} and r.opinion == "modified" and r.upper_limit >= 1000


def test_metadata_changes_cannot_remove_cutoff_flag_and_pass_external_verification(tmp_path):
    led = Ledger.from_entries("F", [make_entry("A", "sale", 89, 100, rec_day=92)], BOUNDS)
    p = tmp_path / "l.jsonl"
    led.save(p)
    rows = p.read_text().splitlines()
    header = json.loads(rows[0])
    header["period_bounds"] = [[0, 95], [96, 180], [181, 272], [273, 364]]
    rows[0] = json.dumps(header)
    p.write_text("\n".join(rows) + "\n")
    changed = Ledger.load(p)
    assert led.cutoff_flags() == [0] and changed.cutoff_flags() == []
    assert not changed.verify(led.commits).ok and not changed.verify(led.commits).metadata_ok


def test_commitment_size_is_validated():
    led = Ledger.from_entries("F", [make_entry("A", "sale", 5, 100)], BOUNDS)
    cs = {k: replace(c, size=c.size + 1) for k, c in led.commits.items()}
    assert not led.verify(cs).ok


def test_empty_independent_commitments_rejected(tmp_path, capsys):
    p = tmp_path / "l.jsonl"
    Ledger.from_entries("F", [make_entry("A", "sale", 5, 100)], BOUNDS).save(p)
    c = tmp_path / "empty.json"
    c.write_text("[]")
    assert main(["verify", str(p), "--commits", str(c)]) == 2
    assert "non-empty" in capsys.readouterr().err


def test_all_records_deleted_in_memory_returns_report():
    led = Ledger.from_entries("F", [make_entry("A", "sale", 5, 100)], BOUNDS)
    led.records.clear()
    r = led.verify()
    assert not r.ok and r.missing_records == 1


@pytest.mark.parametrize("first_day", [20, 25])
def test_following_year_proofs_exist_when_tail_already_covered_and_when_checkpoint_updated(first_day):
    led = Ledger.from_entries(
        "F", [make_entry("A", "sale", 5, 100), make_entry("B", "sale", first_day, 100)], [(0, 9), (10, 19)]
    )
    k, proof = led.prove("B")
    assert led.verify_inclusion(led.records[1], k, proof, commitment=led.commits[k])
    led.append(make_entry("C", "sale", first_day + 1, 100))
    led.checkpoint()
    k, proof = led.prove("C")
    assert led.verify_inclusion(led.records[2], k, proof, commitment=led.commits[k]) and led.verify().ok


@pytest.mark.parametrize(
    "field,value",
    [
        ("date", "2025-04-01"),
        ("record_date", "2025-04-02"),
        ("counterparty", "S"),
        ("doc_id", "OTHER"),
        ("kind", "purchase"),
    ],
)
def test_conflicting_posting_metadata_is_rejected(tmp_path, field, value):
    import csv

    a = dict(
        entry_id="A",
        date="2025-03-31",
        record_date="2025-03-31",
        account="1500",
        debit="100",
        credit="0",
        kind="sale",
        counterparty="C",
        doc_id="INV",
    )
    b = dict(a, account="3000", debit="0", credit="100")
    b[field] = value
    p = tmp_path / "x.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=a)
        w.writeheader()
        w.writerows([a, b])
    with pytest.raises(ValueError, match="inconsistent"):
        read_gl_csv(p)


def test_committed_rounding_must_preserve_balance():
    e = Entry("A", "sale", 0, 0, [Line("1500", 0.005, 0), Line("1501", 0.005, 0), Line("3000", 0, 0.01)], 0.01)
    with pytest.raises(ValueError, match="rounding"):
        Ledger.from_entries("F", [e], BOUNDS)


def test_amount_cannot_disagree_with_postings():
    with pytest.raises(ValueError, match="total debit"):
        Entry("A", "sale", 0, 0, [Line("1500", 100, 0), Line("3000", 0, 100)], 10000)


def test_no_truth_removes_old_labels(tmp_path):
    e = economy([fictitious()])
    save_economy(e, tmp_path)
    save_economy(e, tmp_path, truth=False)
    assert not (tmp_path / "truth.csv").exists()


@pytest.mark.parametrize(
    "d",
    [
        {"replications": 1.5},
        {"seed": -1},
        {"regimes": ["sampling", "sampling"]},
        {"economy": {"days": 120.5}},
        {"economy": {"periods": 400}},
        {"economy": {"record_lag_probs": []}},
        {"costs": {"per_item": float("nan")}},
        {"regime_params": {"ledger": {"close_lag": -1}}},
        {"regime_params": {"crossledger": {"psi_backend": "typo"}}},
        {"misstatements": {"tamper_factor": -2}},
        {"experiment": "lending", "options": {"advance_verified": -1}},
        {"experiment": "lending", "options": {"advance_unverified": 0.9}},
        {"experiment": "disclosure", "regime_params": {"periodic": {"annual_lag": -1}}},
    ],
)
def test_invalid_scenarios_fail_before_execution(d):
    with pytest.raises(ValueError):
        Scenario.from_dict(d)


def test_quarterly_lending_waits_for_recording_day():
    from veriledger.lending import lend

    p = make_entry("P", "purchase", 89, 1000, rec_day=92, counterparty="S", doc_id="PI")
    e = economy([p], {"S": [CounterRecord("S", "PI", 89, 1000, "purchase")]})
    r = lend(e, "ledger", opening_days=0)
    assert r.credit == pytest.approx(600) and r.shortfall == pytest.approx(200)


def test_corrected_purchase_retains_verified_true_component():
    from veriledger.lending import lend

    p = make_entry("P", "purchase", 5, 1500, counterparty="S", doc_id="PI")
    p.true_amount, p.misstatement = 1000, "inflate"
    e = economy([p], {"S": [CounterRecord("S", "PI", 5, 1000, "purchase")]})
    r = lend(e, "ledger", opening_days=0)
    assert r.credit == pytest.approx(800) and r.verified_share == 1 and r.shortfall == 0


def test_snapshot_does_not_apply_future_database_alteration():
    p = make_entry("P", "purchase", 5, 1000, counterparty="S", doc_id="PI")
    p.misstatement = "tamper"
    e = economy([p])
    e.tampering, e.tamper_days = {"P": 1500}, {"P": 95}
    before, after = e.as_of(89), e.as_of(100)
    assert not before.tampering and before.entries[0].misstatement is None
    assert after.apparent_entries()[0].amount == 1500


@pytest.mark.parametrize("correct", [False, True])
def test_annual_report_applies_annual_findings_to_all_quarters(correct):
    f = fictitious()
    g = make_entry("G", "sale", 6, 1000, counterparty="C", doc_id="G")
    e = economy([f, g])
    r = periodic(
        e,
        audit=SamplingAudit(tolerable=0.01, correct=correct),
        annual_lag=0,
        detection=DetectionModel(vouch={"fictitious": 1.0}, review={"fictitious": 0.0}),
    )
    assert r.annual_reported == (1000 if correct else 2000)
    assert r.outsider[-1] == r.annual_reported
    assert r.audit["residual_overstatement_annual"] == (0 if correct else 1000)


def test_attribute_residual_does_not_invent_a_monetary_opinion():
    e = economy([make_entry("A", "sale", 5, 1000, counterparty="C", doc_id="INV")])
    e.parties["C"].adopts = False
    r = CrossLedgerAudit(residual=SamplingAudit(method="attribute")).run(e)
    assert r.opinion == "none"
