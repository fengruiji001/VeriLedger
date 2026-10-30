"""Tests for trust boundaries, statistical tails and data exchange."""

import json
import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

from veriledger.audit import (
    CrossLedgerAudit,
    DetectionModel,
    SamplingAudit,
    attribute_sample_size,
    poisson_upper,
    reliability_factor,
)
from veriledger.cli import main
from veriledger.disclosure import attested
from veriledger.formats import load_economy, read_saft, save_economy, write_saft
from veriledger.ledger import Ledger, canonical
from veriledger.merkle import Proof, leaf_hash, verify_proof
from veriledger.misstate import inject
from veriledger.model import CounterRecord, Economy, Entry, Line, Party, make_entry, with_amount

BOUNDS = [(0, 4), (5, 9)]


@pytest.mark.parametrize(
    "count,confidence,reference",
    [
        (1, 0.1, 0.531811608389612),
        (3, 0.05, 1.366318396749831),
        (1, 0.9999999999999999, 40.461567483087464),
        (5, 0.9999999999999999, 51.78408121873086),
    ],
)
def test_poisson_upper_brackets_low_and_high_confidence(count, confidence, reference):
    assert poisson_upper(count, confidence) == pytest.approx(reference, rel=1e-11)


def test_very_small_confidences_do_not_collapse_to_zero():
    assert reliability_factor(1e-20) == pytest.approx(1e-20, rel=1e-12, abs=0)
    assert poisson_upper(0, 1e-20) == pytest.approx(1e-20, rel=1e-12, abs=0)
    assert attribute_sample_size(confidence=1e-20, tolerable_rate=1e-20) == 1


def example_ledger():
    return Ledger.from_entries(
        "F",
        [make_entry("A", "sale", 0, 100), make_entry("B", "sale", 1, 100, rec_day=6), make_entry("C", "sale", 11, 100)],
        BOUNDS,
        close_lag=0,
    )


def test_checkpoint_only_cannot_authenticate_late_status(tmp_path):
    led = example_ledger()
    path = tmp_path / "ledger.jsonl"
    led.save(path)
    rows = path.read_text().splitlines()
    row = json.loads(rows[2])
    row["late"] = False
    rows[2] = json.dumps(row)
    path.write_text("\n".join(rows) + "\n")
    changed = Ledger.load(path)
    result = changed.verify({2: led.commits[2]})
    assert not result.ok and result.commitment_coverage and 0 in result.unanchored_periods
    assert not changed.verify(led.commits).ok


def test_inclusion_requires_the_explicit_trusted_commitment():
    led = example_ledger()
    trusted = dict(led.commits)
    fake = make_entry("FAKE", "sale", 2, 999).record()
    led.commits[0] = replace(led.commits[0], root=leaf_hash(canonical(fake)), size=1)
    proof = Proof(0, 1, ())
    assert led.verify(trusted).ok
    with pytest.raises(ValueError, match="explicit commitment"):
        led.verify_inclusion(fake, 0, proof)
    assert not led.verify_inclusion(fake, 0, proof, commitment=trusted[0])
    assert led.verify_inclusion(fake, 0, proof, commitment=led.commits[0])  # local consistency only
    period, valid = led.prove("A")
    assert led.verify_inclusion(led.records[0], period, valid, commitment=trusted[period])


def test_malformed_proofs_and_out_of_range_periods_are_rejected():
    led = example_ledger()
    period, proof = led.prove("A")
    record = led.records[0]
    root = led.commits[period].root
    leaf = leaf_hash(canonical(record))
    assert not verify_proof(leaf, Proof(True, 1, ()), root)
    assert not verify_proof(leaf, Proof(0, 1, (b"bad",)), root)
    with pytest.raises(ValueError, match="integers"):
        Proof.from_dict({"index": 0.8, "size": 1, "path": []})
    fabricated = replace(led.commits[period], period=99)
    assert not led.verify_inclusion(record, 99, proof, commitment=fabricated)


def test_cli_does_not_claim_external_custody_is_independent(tmp_path, capsys):
    led = example_ledger()
    p = tmp_path / "l.jsonl"
    led.save(p)
    assert main(["verify", str(p), "--commits", str(p) + ".commits.json"]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["external_commitments_supplied"] is True
    assert result["independent_commitments"] is None and result["trust_verified"] is False
    assert "not proof of independent custody" in output.err


def test_no_truth_export_removes_tamper_event_labels(tmp_path):
    e = make_entry("T", "sale", 1, 100, counterparty="C", doc_id="I")
    e.misstatement = "tamper"
    econ = Economy(
        "F", [e], {"C": Party("C", "customer", True)}, {}, BOUNDS, tampering={"T": 150}, tamper_days={"T": 7}
    )
    save_economy(econ, tmp_path, truth=True)
    save_economy(econ, tmp_path, truth=False)
    assert not (tmp_path / "truth.csv").exists()
    assert "tamper_days" not in json.loads((tmp_path / "economy.json").read_text())
    assert not load_economy(tmp_path).tampering


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "nan"])
def test_corrupt_truth_sidecar_is_rejected(tmp_path, mutation):
    entries = [make_entry("A", "sale", 1, 100), make_entry("B", "sale", 2, 200)]
    entries[1].misstatement, entries[1].true_amount = "inflate", 50
    save_economy(Economy("F", entries, {}, {}, BOUNDS), tmp_path)
    path = tmp_path / "truth.csv"
    rows = path.read_text().splitlines()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[1])
    elif mutation == "unknown":
        rows.append(rows[1].replace("A,", "UNKNOWN,", 1))
    else:
        parts = rows[1].split(",")
        parts[2] = "nan"
        rows[1] = ",".join(parts)
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(ValueError):
        load_economy(tmp_path)


@pytest.mark.parametrize("doc", [None, "", "   "])
def test_blank_document_id_never_counts_as_confirmation(doc):
    e = make_entry("A", "sale", 1, 100, counterparty="C", doc_id=doc)
    e.true_amount, e.misstatement = 0, "fictitious"
    econ = Economy(
        "F", [e], {"C": Party("C", "customer", True)}, {"C": [CounterRecord("C", doc, 1, 100, "sale")]}, BOUNDS
    )
    r = CrossLedgerAudit(residual_sampling=False).run(econ)
    assert r.checked == {"A"} and not r.confirmed and "A" in r.detected
    daily = attested(econ, detection=DetectionModel(investigate={"fictitious": 1}), report_lag=0)
    assert daily.audit["matched"] == 0 and daily.outsider[2] == 0


@pytest.mark.parametrize("method", ["attribute", "random"])
def test_non_monetary_residual_does_not_issue_monetary_opinion_without_remainder(method):
    e = make_entry("A", "sale", 1, 100, counterparty="C", doc_id="I")
    econ = Economy(
        "F", [e], {"C": Party("C", "customer", True)}, {"C": [CounterRecord("C", "I", 1, 100, "sale")]}, BOUNDS
    )
    r = CrossLedgerAudit(residual=SamplingAudit(method=method)).run(econ)
    assert r.confirmed == {"A"} and r.opinion == "none"


def test_zero_standard_entry_can_be_scaled_and_tamper_bundle_roundtrips(tmp_path):
    e = make_entry("Z", "sale", 1, 0, counterparty="C", doc_id="I")
    altered = with_amount(e, 100)
    assert altered.is_balanced() and altered.debits == altered.credits == 100
    e.misstatement = "tamper"
    econ = Economy(
        "F", [e], {"C": Party("C", "customer", True)}, {}, BOUNDS, tampering={"Z": 100}, tamper_days={"Z": 7}
    )
    save_economy(econ, tmp_path)
    back = load_economy(tmp_path)
    assert back.entries[0].amount == 0
    assert back.apparent_entries()[0].amount == 100 and back.apparent_entries()[0].is_balanced()


def test_tamper_event_cannot_precede_delayed_posting():
    e = make_entry("A", "sale", 4, 100, rec_day=20)
    econ = Economy("F", [e], {}, {}, BOUNDS, last_day=30)
    out = inject(econ, {"tamper": 1.0}, seed=3, renumber=False)
    assert out.tamper_days["A"] == 20
    assert not out.as_of(19).entries and out.as_of(20).tampering == {"A": 130.0}
    out.tamper_days["A"] = 10  # older bundles may contain an earlier date
    assert out.as_of(20).tamper_days["A"] == 20


def test_amount_scaling_rejects_ambiguous_posting_structures():
    many = Entry("M", "sale", 0, 0, [Line("1500", debit=40), Line("1501", debit=60), Line("3000", credit=100)], 100)
    with pytest.raises(ValueError, match="two-line"):
        with_amount(many, 80)
    unknown = Entry("U", "other", 0, 0, [Line("X"), Line("Y")], 0)
    with pytest.raises(ValueError, match="cannot infer"):
        with_amount(unknown, 80)


def test_saft_prefers_invoice_reference_across_all_lines(tmp_path):
    path = tmp_path / "file.xml"
    write_saft([make_entry("A", "sale", 1, 100)], path)
    tree = ET.parse(path)
    lines = [n for n in tree.getroot().iter() if n.tag.endswith("}Line")]
    ns = lines[0].tag.split("}")[0] + "}"
    ET.SubElement(lines[0], ns + "SourceDocumentID").text = "BATCH"
    ET.SubElement(lines[1], ns + "ReferenceNumber").text = "INVOICE"
    tree.write(path)
    assert read_saft(path)[0].doc_id == "INVOICE"


def test_saft_ambiguous_invoices_and_parties_are_not_silently_merged(tmp_path):
    path = tmp_path / "file.xml"
    write_saft([make_entry("A", "sale", 1, 100)], path)
    tree = ET.parse(path)
    lines = [n for n in tree.getroot().iter() if n.tag.endswith("}Line")]
    ns = lines[0].tag.split("}")[0] + "}"
    for line, reference, party in zip(lines, ("I1", "I2"), ("C1", "C2")):
        ET.SubElement(line, ns + "ReferenceNumber").text = reference
        ET.SubElement(line, ns + "CustomerID").text = party
    tree.write(path)
    e = read_saft(path)[0]
    assert e.doc_id is None and e.counterparty is None
