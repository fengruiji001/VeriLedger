"""Tests for nested audit accounting, SAF-T evidence and sampling domains."""

import xml.etree.ElementTree as ET

import numpy as np
import pytest

from veriledger.audit import (
    CrossLedgerAudit,
    DetectionModel,
    LedgerAudit,
    SamplingAudit,
    mus_evaluate,
    mus_sample_size,
    mus_select,
)
from veriledger.formats import SAFT_NS, read_saft, write_saft
from veriledger.ledger import Ledger
from veriledger.lending import lend
from veriledger.model import CounterRecord, Economy, Party, make_entry

BOUNDS = [(0, 4), (5, 9)]


def test_lending_preserves_field_exam_procedure_counts():
    entries = [make_entry("A", "purchase", 1, 100, doc_id="INV"), make_entry("B", "purchase", 2, 100, doc_id="INV")]
    econ = Economy("F", entries, {}, {}, BOUNDS)
    result = lend(econ, "field_exam", opening_days=0, exam=SamplingAudit(duplicate_test=True))
    assert result.audit["sampled"] == 2
    assert result.audit["investigated"] == result.audit["false_positives"] == 1


def test_pending_confirmed_exception_is_not_verified_collateral():
    e = make_entry("A", "purchase", 4, 100, rec_day=5, counterparty="S", doc_id="INV")
    e.misstatement, e.true_amount = "round_trip", 0
    econ = Economy(
        "F", [e], {"S": Party("S", "supplier", True)}, {"S": [CounterRecord("S", "INV", 4, 100, "purchase")]}, BOUNDS
    )
    result = lend(econ, "ledger", opening_days=0, detection=DetectionModel(investigate={"round_trip": 0}))
    assert result.audit["unresolved"] == result.audit["investigated"] == 1
    assert result.verified_share == 0
    assert result.credit == pytest.approx(25)


@pytest.mark.parametrize("correct", [True, False])
def test_residual_detection_resolves_initial_exception(correct):
    e = make_entry("A", "sale", 4, 100, rec_day=5)
    e.misstatement, e.true_amount = "fictitious", 0
    econ = Economy("F", [e], {}, {}, BOUNDS)
    result = CrossLedgerAudit(correct=correct).run(
        econ, detection=DetectionModel(vouch={"fictitious": 1}, investigate={"fictitious": 0})
    )
    assert result.detected == {"A"}
    assert result.corrected == ({"A"} if correct else set())
    assert result.unresolved == set()
    assert result.opinion == ("clean" if correct else "modified")
    assert result.known_misstatement == pytest.approx(100)


@pytest.mark.parametrize("label", [None, "duplicate"])
def test_residual_investigation_counts_and_bounds_are_merged_once(label):
    a = make_entry("A", "sale", 1, 100, doc_id="INV")
    b = make_entry("B", "sale", 2, 100, doc_id="INV")
    b.misstatement = label
    if label:
        b.true_amount = 0
    econ = Economy("F", [a, b], {}, {}, BOUNDS)
    sample = SamplingAudit(tolerable=0.5, confidence=0.5, duplicate_test=True, cutoff_per_boundary=0)
    det = DetectionModel(vouch={"duplicate": 0}, investigate={"duplicate": 0})
    expected = sample.run(econ, rng=7, detection=det, tolerable_amount=100)
    result = CrossLedgerAudit(ledger_checks=False, residual=sample).run(econ, rng=7, detection=det)
    assert result.upper_limit == pytest.approx(expected.upper_limit)
    assert result.flagged == expected.flagged == {"B"}
    assert result.investigated == expected.investigated == 1
    assert result.false_positives == expected.false_positives
    assert result.cleared == expected.cleared
    assert result.unresolved == expected.unresolved
    assert result.known_misstatement == pytest.approx(expected.known_misstatement)


def test_omitted_direct_detection_probability_does_not_mean_certainty():
    e = make_entry("A", "sale", 4, 100, rec_day=5)
    e.misstatement, e.true_amount = "fictitious", 0
    econ = Economy("F", [e], {}, {}, BOUNDS)
    result = LedgerAudit().run(econ, detection=DetectionModel(investigate={}))
    assert result.detected == set() and result.unresolved == {"A"}


def test_saft_source_user_is_not_an_invoice_identifier(tmp_path):
    path = tmp_path / "saft.xml"
    entries = [make_entry("A", "sale", 1, 100, counterparty="C"), make_entry("B", "sale", 2, 200, counterparty="C")]
    write_saft(entries, path)
    tree = ET.parse(path)
    for transaction in tree.getroot().iter(f"{{{SAFT_NS}}}Transaction"):
        ET.SubElement(transaction, f"{{{SAFT_NS}}}SourceID").text = "ADMIN"
    tree.write(path)
    imported = read_saft(path, start_date="2025-01-01")
    assert all(e.doc_id is None for e in imported)
    assert not Ledger.from_entries("F", imported, BOUNDS).duplicate_flags()


def test_zero_value_population_has_no_monetary_unit_selection():
    assert mus_select(np.zeros(3), 10, np.random.default_rng(1)).size == 0


@pytest.mark.parametrize(
    "amounts,n", [([-1, 10], 2), ([float("nan"), 10], 2), ([[1, 2]], 2), ([1, 2], 1.5), ([1, 2], -1)]
)
def test_mus_select_rejects_invalid_populations_and_counts(amounts, n):
    with pytest.raises(ValueError):
        mus_select(np.array(amounts), n, np.random.default_rng(1))


@pytest.mark.parametrize(
    "kwargs",
    [{"expected": -1}, {"expansion": -1}, {"book_value": -1}, {"confidence": True}, {"tolerable": float("inf")}],
)
def test_sample_size_requires_valid_finite_assumptions(kwargs):
    params = {"book_value": 100, "tolerable": 10, **kwargs}
    with pytest.raises(ValueError):
        mus_sample_size(**params)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"taintings": [-0.1]},
        {"taintings": [1.1]},
        {"taintings": [float("nan")]},
        {"interval": -1},
        {"top_stratum": -1},
        {"known": -1},
    ],
)
def test_stringer_evaluation_rejects_invalid_error_inputs(kwargs):
    params = {"taintings": [], "top_stratum": 0, "interval": 10, **kwargs}
    with pytest.raises(ValueError):
        mus_evaluate(**params)
