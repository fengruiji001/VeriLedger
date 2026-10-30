"""Tests for publication evidence, missing documents and injection identities."""

import pytest

from veriledger.audit import DetectionModel, SamplingAudit
from veriledger.disclosure import periodic, public
from veriledger.ledger import Ledger
from veriledger.misstate import inject, renumber_economy
from veriledger.model import CounterRecord, Economy, Party, make_entry

BOUNDS = [(0, 4), (5, 9)]


def economy(entries):
    parties = {
        "C": Party("C", "customer"),
        "S": Party("S", "supplier"),
        "R": Party("R", "customer", adopts=True, related=True),
    }
    mirrors = {
        "C": [CounterRecord("C", e.doc_id, e.eff_day, e.amount, e.kind) for e in entries if e.counterparty == "C"]
    }
    return Economy("F", entries, parties, mirrors, BOUNDS)


@pytest.mark.parametrize("misstated", [False, True])
def test_periodic_preserves_annual_investigation_counts(misstated):
    a = make_entry("A", "sale", 1, 100, doc_id="I")
    b = make_entry("B", "sale", 2, 100, doc_id="I")
    if misstated:
        b.misstatement, b.true_amount = "duplicate", 0
    r = periodic(
        economy([a, b]),
        report_lag=0,
        audit=SamplingAudit(duplicate_test=True),
        detection=DetectionModel(review={}, vouch={"duplicate": 0}, investigate={"duplicate": 0}),
    )
    assert r.audit["investigated"] == 1
    assert r.audit["false_positives"] == int(not misstated)
    assert r.audit["unresolved"] == int(misstated)
    assert r.audit["sampled"] == 2


@pytest.mark.parametrize("future", ["posting", "alteration"])
def test_periodic_opinion_is_scored_on_publication_snapshot(future):
    a = make_entry("A", "sale", 1, 100)
    e = economy([a])
    if future == "posting":
        f = make_entry("F", "sale", 1, 100, rec_day=100)
        f.misstatement, f.true_amount = "fictitious", 0
        e.entries.append(f)
    else:
        a.misstatement = "tamper"
        e.tampering["A"], e.tamper_days["A"] = 200, 100
    r = periodic(e, report_lag=0, detection=DetectionModel(review={}))
    assert r.annual_reported == 100
    assert r.audit["opinion"] == "clean"
    assert r.audit["population"] == 1
    assert r.audit["n_misstated"] == 0
    assert r.audit["residual_overstatement_annual"] == 0
    assert r.audit["audit_failure"] == 0


@pytest.mark.parametrize("doc", ["   ", "\t\n"])
def test_blank_document_ids_cannot_delete_genuine_revenue(doc):
    entries = [make_entry("A", "sale", 1, 100, doc_id=doc), make_entry("B", "sale", 2, 100, doc_id=doc)]
    e = economy(entries)
    assert Ledger.from_entries("F", entries, BOUNDS).duplicate_flags() == []
    assert SamplingAudit(duplicate_test=True).run(e).investigated == 0
    assert public(e).outsider[-1] == 200


@pytest.mark.parametrize("scheme,prefix", [("duplicate", "D"), ("fictitious", "F"), ("round_trip", "R")])
@pytest.mark.parametrize("renumber", [False, True])
def test_injection_entry_ids_cannot_collide_with_imported_ids(scheme, prefix, renumber):
    original = make_entry(f"~{prefix}000001", "sale", 1, 100, counterparty="C", doc_id="I")
    e = economy([original])
    m = inject(e, {scheme: 1}, renumber=renumber)
    assert len({x.entry_id for x in m.entries}) == len(m.entries)
    assert Ledger.from_entries(m.firm, m.entries, m.period_bounds).verify().ok
    assert e.entries[0].entry_id == f"~{prefix}000001"


def test_repeated_unrenumbered_injection_keeps_unique_entry_ids():
    e = economy([make_entry("A", "sale", 1, 100, counterparty="C", doc_id="I")])
    first = inject(e, {"duplicate": 1}, renumber=False)
    second = inject(first, {"duplicate": 1}, renumber=False)
    assert len(second.entries) == 3
    assert len({x.entry_id for x in second.entries}) == 3


@pytest.mark.parametrize("renumber", [False, True])
@pytest.mark.parametrize("where", ["entry", "mirror"])
def test_fictitious_invoice_does_not_reuse_existing_reference(renumber, where):
    doc = "INV-~F000001" if where == "entry" else "I"
    e = economy([make_entry("A", "sale", 1, 100, counterparty="C", doc_id=doc)])
    if where == "mirror":
        e.mirrors["C"].append(CounterRecord("C", "INV-~F000001", 1, 100, "sale"))
    m = inject(e, {"fictitious": 1}, renumber=renumber)
    genuine = next(x for x in m.entries if x.kind == "sale" and not x.misstatement)
    fictitious = next(x for x in m.entries if x.kind == "sale" and x.misstatement)
    assert genuine.doc_id != fictitious.doc_id
    assert m.mirrors["C"][0].doc_id == genuine.doc_id
    assert fictitious.doc_id not in {record.doc_id for record in m.mirrors["C"]}


@pytest.mark.parametrize("renumber", [False, True])
def test_round_trip_documents_do_not_reuse_existing_references(renumber):
    e = economy(
        [
            make_entry("A", "sale", 1, 100, counterparty="C", doc_id="INV-~R000001"),
            make_entry("B", "purchase", 1, 100, counterparty="S", doc_id="PI-~R000003"),
        ]
    )
    m = inject(e, {"round_trip": 1}, renumber=renumber)
    for kind in ("sale", "purchase"):
        genuine = next(x for x in m.entries if x.kind == kind and not x.misstatement)
        artificial = next(x for x in m.entries if x.kind == kind and x.misstatement)
        assert genuine.doc_id != artificial.doc_id


@pytest.mark.parametrize("field,value", [("description", {"bad": {1, 2}}), ("doc_id", b"not-json"), ("lines", [1j])])
def test_nonserializable_damage_returns_integrity_failure(field, value):
    entries = [make_entry("A", "sale", 1, 100), make_entry("B", "sale", 6, 200)]
    ledger = Ledger.from_entries("F", entries, BOUNDS)
    ledger.tamper("A", **{field: value})
    report = ledger.verify()
    assert not report.ok and not report.chain_ok
    assert report.modified == [0]
    assert report.errors and report.bad_periods


def test_circular_damage_returns_integrity_failure():
    ledger = Ledger.from_entries("F", [make_entry("A", "sale", 1, 100)], BOUNDS)
    value = []
    value.append(value)
    ledger.tamper("A", description=value)
    report = ledger.verify()
    assert not report.ok and report.modified == [0]


def test_neutral_renumbering_reserves_unmapped_peer_references():
    e = economy(
        [
            make_entry("A", "sale", 1, 100, counterparty="C", doc_id="I"),
            make_entry("B", "sale", 2, 200, counterparty="C", doc_id="J"),
        ]
    )
    e.mirrors["C"].append(CounterRecord("C", "INV-000002", 2, 300, "sale"))
    renumber_economy(e)
    assert e.mirrors["C"][-1].doc_id == "INV-000002"
    assert "INV-000002" not in {entry.doc_id for entry in e.entries}
    assert [record.doc_id for record in e.mirrors["C"][:2]] == [entry.doc_id for entry in e.entries]
