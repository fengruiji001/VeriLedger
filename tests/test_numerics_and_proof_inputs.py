"""Tests for large Poisson limits, simulation inputs and proof boundaries."""

from dataclasses import replace

import pytest

from veriledger.audit import AuditResult, CrossLedgerAudit, DetectionModel, SamplingAudit, poisson_upper
from veriledger.disclosure import public
from veriledger.ledger import Ledger
from veriledger.lending import lend
from veriledger.merkle import Proof, leaf_hash, verify_proof
from veriledger.metrics import evaluate
from veriledger.model import CounterRecord, Economy, Party, make_entry
from veriledger.scenario import Scenario, run_once

BOUNDS = [(0, 4), (5, 9)]


@pytest.mark.parametrize(
    "count,confidence,reference",
    [
        (6000, 0.75, 6053.066836685232),
        (6000, 0.95, 6128.9864075982805),
        (10000, 0.75, 10068.269434814592),
        (10000, 0.95, 10166.060136127071),
    ],
)
def test_large_poisson_limits_match_independent_gamma_quantiles(count, confidence, reference):
    assert poisson_upper(count, confidence) == pytest.approx(reference, rel=1e-10)


def test_poisson_limit_increases_across_the_previous_overflow_boundary():
    assert poisson_upper(6000) > poisson_upper(5000) > 5000


@pytest.mark.parametrize("runner", ["evaluate", "sampling", "public", "lending"])
@pytest.mark.parametrize("problem", ["amount", "day", "label", "flag"])
def test_simulation_and_scoring_reject_invalid_truth_fields(runner, problem):
    kind = "purchase" if runner == "lending" else "sale"
    e = make_entry("A", kind, 1, 100)
    e.misstatement = "inflate"
    econ = Economy("F", [e], {}, {}, BOUNDS)
    if problem == "amount":
        e.true_amount = float("nan")
    elif problem == "day":
        e.true_day = True
    elif problem == "label":
        e.misstatement = "typo"
    else:
        econ.has_ground_truth = "yes"
    with pytest.raises(ValueError):
        if runner == "evaluate":
            evaluate(econ, AuditResult("check", 1, opinion="clean", tolerable_amount=3), kind)
        elif runner == "sampling":
            SamplingAudit().run(econ, detection=DetectionModel(vouch={}, investigate={}))
        elif runner == "public":
            public(econ, report_lag=0)
        else:
            lend(econ, "trust", opening_days=0)


@pytest.mark.parametrize(
    "changes",
    [{"day": 1.9}, {"day": True}, {"amount": True}, {"amount": float("nan")}, {"doc_id": 7}, {"kind": "unknown"}],
)
def test_invalid_counterparty_evidence_cannot_be_used_for_matching(changes):
    with pytest.raises(ValueError):
        record = CounterRecord(**{**dict(owner="C", doc_id="I", day=1, amount=1, kind="sale"), **changes})
        entry = make_entry("A", "sale", 1, 1, counterparty="C", doc_id="I")
        econ = Economy("F", [entry], {"C": Party("C", "customer", True)}, {"C": [record]}, BOUNDS)
        CrossLedgerAudit(ledger_checks=False, residual_sampling=False).run(econ)


@pytest.mark.parametrize("mode", ["raw", "ledger"])
@pytest.mark.parametrize("problem", ["missing", "null_path", "iterator"])
def test_malformed_or_consumable_proofs_are_rejected(mode, problem):
    leaf = leaf_hash(b"x")
    if problem == "missing":
        proof = None
    else:
        proof = Proof(0, 1, None if problem == "null_path" else iter([b"a" * 32]))
    if mode == "raw":
        assert verify_proof(leaf, proof, leaf) is False
    else:
        led = Ledger.from_entries("F", [make_entry("A", "sale", 1, 100)], BOUNDS)
        assert led.verify_inclusion(led.records[0], 0, proof, commitment=led.commits[0]) is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("period", True),
        ("period", 1.0),
        ("size", True),
        ("size", 1.0),
        ("n_records", False),
        ("n_records", 0),
        ("commit_day", True),
        ("commit_day", -1),
        ("chain_head", b""),
    ],
)
def test_inclusion_rejects_structurally_invalid_commitments(field, value):
    led = Ledger.from_entries("F", [make_entry("A", "sale", 5, 100)], BOUNDS)
    k, proof = led.prove("A")
    bad = replace(led.commits[k], **{field: value})
    assert led.verify_inclusion(led.records[0], k, proof, commitment=bad) is False


@pytest.mark.parametrize("branch", ["crossledger", "residual", "periodic"])
def test_nested_parameter_sweep_can_override_explicit_null_defaults(branch):
    small = dict(days=10, periods=2, post_days=0, n_customers=4, n_suppliers=3, related_parties=0)
    if branch == "periodic":
        config = dict(experiment="disclosure", regimes=["periodic"], regime_params={"periodic": {"audit": None}})
        key = "regime_params.periodic.audit.confidence"
    else:
        params = None if branch == "crossledger" else {"residual": None}
        config = dict(regimes=["crossledger"], regime_params={"crossledger": params})
        key = "regime_params.crossledger.residual.confidence"
    scn = Scenario.from_dict({**config, "economy": small, "replications": 1})
    before = scn.to_dict()
    variant = scn.with_value(key, 0.8)
    first, nested = ("periodic", "audit") if branch == "periodic" else ("crossledger", "residual")
    assert variant.regime_params[first][nested]["confidence"] == 0.8
    assert scn.to_dict() == before
    assert len(run_once(variant, 0)) == 1


def test_sweep_path_through_a_scalar_has_a_clear_validation_error():
    scn = Scenario.from_dict({"economy": {"days": 10, "periods": 2}})
    with pytest.raises(ValueError, match="mapping"):
        scn.with_value("economy.days.confidence", 0.8)
