from collections import Counter

import numpy as np
import pytest
from conftest import SMALL

from veriledger.misstate import MisstatementSpec, inject, overstatement
from veriledger.synth import EconomyConfig, generate, period_bounds


def test_deterministic(small_cfg):
    a, b = generate(small_cfg, seed=5), generate(small_cfg, seed=5)
    assert [e.record() for e in a.entries] == [e.record() for e in b.entries]
    c = generate(small_cfg, seed=6)
    assert [e.record() for e in a.entries] != [e.record() for e in c.entries]


def test_all_entries_balanced_and_mirrored(econ):
    assert all(e.is_balanced() for e in econ.entries)
    mirror = {(m.doc_id, m.kind): m for recs in econ.mirrors.values() for m in recs}
    for e in econ.entries:
        if e.kind in ("sale", "purchase"):
            m = mirror[(e.doc_id, e.kind)]
            assert m.owner == e.counterparty and m.amount == e.amount and m.day in (e.eff_day, e.eff_day + 1)


def test_period_bounds_cover_year():
    b = period_bounds(360, 4)
    assert b[0][0] == 0 and b[-1][1] == 359
    assert all(b[k][1] + 1 == b[k + 1][0] for k in range(3))


@pytest.mark.parametrize(
    "bad", [dict(adoption=1.5), dict(days=2, periods=4), dict(related_parties=99), dict(record_lag_probs=(0.5, 0.2))]
)
def test_config_validation(bad):
    with pytest.raises(ValueError):
        generate(EconomyConfig(**{**SMALL, **bad}))


def test_unknown_config_key():
    with pytest.raises(ValueError):
        EconomyConfig.from_dict({"nope": 1})
    assert EconomyConfig.from_dict({"record_lag_probs": [1.0]}).record_lag_probs == (1.0,)
    assert EconomyConfig().to_dict()["adoption"] == 0.5


def test_adoption_share_and_nesting():
    sets = []
    for a in (0.0, 0.25, 0.5, 1.0):
        e = generate(EconomyConfig(**SMALL, adoption=a), seed=3)
        cust = [p for p in e.parties.values() if p.role == "customer" and not p.related]
        sets.append({p.party_id for p in e.parties.values() if p.adopts and not p.related})
        if a == 1.0:
            assert all(p.adopts for p in cust)
        assert all(p.adopts for p in e.parties.values() if p.related)
    assert sets[0] <= sets[1] <= sets[2] <= sets[3]


def test_common_random_numbers_across_adoption():
    a = generate(EconomyConfig(**SMALL, adoption=0.0), seed=9)
    b = generate(EconomyConfig(**SMALL, adoption=1.0), seed=9)
    assert [e.record() for e in a.entries] == [e.record() for e in b.entries]


def test_adoption_by_size():
    e = generate(EconomyConfig(**SMALL, adoption=0.25, adoption_by_size=True), seed=1)
    assert e.parties["C001"].adopts and e.parties["S001"].adopts


def test_no_spec_no_change(econ):
    m = inject(econ, None, seed=1)
    assert [x.record() for x in m.entries] == [x.record() for x in econ.entries]
    assert not m.misstated()


def test_each_scheme(econ):
    spec = MisstatementSpec(backdate=0.5, fictitious=0.05, inflate=0.05, duplicate=0.05, round_trip=2, tamper=0.05)
    m = inject(econ, spec, seed=2)
    counts = Counter(e.misstatement for e in m.entries if e.misstatement)
    assert set(counts) == {"backdate", "fictitious", "inflate", "duplicate", "round_trip", "tamper"}
    assert counts["round_trip"] == 6  # sale, its cost of sales and the purchase back
    assert Counter(e.kind for e in m.entries if e.misstatement == "round_trip") == {"sale": 2, "cogs": 2, "purchase": 2}
    mirror = {(r.doc_id, r.kind): r for recs in m.mirrors.values() for r in recs}
    for e in m.entries:
        if e.misstatement == "fictitious":
            assert (e.doc_id, e.kind) not in mirror and e.true_amount == 0
        elif e.misstatement == "inflate":
            assert mirror[(e.doc_id, e.kind)].amount == e.true_amount < e.amount
        elif e.misstatement == "backdate":
            assert m.period_of(e.eff_day) == m.period_of(e.true_day) - 1
            assert e.rec_day >= e.true_day
        elif e.misstatement == "duplicate":
            assert sum(1 for x in m.entries if x.doc_id == e.doc_id and x.kind == e.kind) == 2
        elif e.misstatement == "round_trip" and e.kind != "cogs":
            assert (e.doc_id, e.kind) in mirror and m.parties[e.counterparty].related
        elif e.misstatement == "tamper":
            assert m.tampering[e.entry_id] > e.amount
    assert all(e.is_balanced() for e in m.entries)
    assert not econ.misstated() and not econ.tampering  # input unchanged


def test_purchase_target(econ):
    m = inject(econ, {"fictitious": 0.05, "inflate": 0.05, "target": "purchase"}, seed=3)
    assert {e.kind for e in m.misstated()} == {"purchase"}
    with pytest.raises(ValueError):
        MisstatementSpec.from_dict({"target": "cogs"})
    with pytest.raises(ValueError):
        MisstatementSpec.from_dict({"unknown": 1})


def test_overstatement_values(misstated):
    for e in misstated.misstated():
        v = overstatement(e, misstated.tampering.get(e.entry_id))
        if e.misstatement in ("fictitious", "duplicate", "round_trip"):
            assert v == pytest.approx(e.amount)
        else:
            assert v > 0


def test_generator_seed_accepted(small_cfg):
    e = generate(small_cfg, np.random.default_rng(0))
    assert len(e.entries) > 100


def test_identifiers_do_not_reveal_labels(misstated):
    import re

    assert all(re.fullmatch(r"E\d{7}", e.entry_id) for e in misstated.entries)
    docs = {e.doc_id for e in misstated.entries if e.kind in ("sale", "purchase")}
    assert all(re.fullmatch(r"(INV|PI)-\d{6}", d) for d in docs)
    assert all("~" not in e.description for e in misstated.entries)


def test_entries_are_numbered_in_recording_order(misstated):
    rec = [e.rec_day for e in sorted(misstated.entries, key=lambda e: e.entry_id)]
    assert rec == sorted(rec)


def test_renumbering_keeps_references_consistent(misstated):
    docs = {e.doc_id for e in misstated.entries if e.kind in ("sale", "purchase")}
    for recs in misstated.mirrors.values():
        assert all(m.doc_id in docs for m in recs)
    for e in misstated.entries:
        if e.kind == "receipt":
            assert e.description.split()[-1] in docs
    ids = {e.entry_id for e in misstated.entries}
    assert set(misstated.tampering) <= ids


def test_backdated_invoice_keeps_its_issue_place(misstated):
    by_doc = {}
    for e in misstated.entries:
        if e.kind == "sale":
            by_doc.setdefault(e.doc_id, e)
    seq = sorted(by_doc.values(), key=lambda e: e.doc_id)
    issue = [e.true_day if e.misstatement == "backdate" else e.eff_day for e in seq]
    assert issue == sorted(issue)


def test_inject_twice(econ):
    a = inject(econ, {"fictitious": 0.02}, seed=1)
    b = inject(a, {"fictitious": 0.02, "duplicate": 0.02}, seed=2)
    ids = [e.entry_id for e in b.entries]
    assert len(ids) == len(set(ids))
    assert sum(e.misstatement == "fictitious" for e in b.entries) > sum(
        e.misstatement == "fictitious" for e in a.entries
    )


def test_inject_guards(econ):
    from veriledger.model import Entry, Line

    no_related = econ.copy()
    for p in no_related.parties.values():
        p.related = False
    with pytest.raises(ValueError, match="related parties"):
        inject(no_related, {"round_trip": 1})
    multi = econ.copy()
    e = next(x for x in multi.entries if x.kind == "sale")
    i = multi.entries.index(e)
    multi.entries[i] = Entry(
        e.entry_id,
        "sale",
        e.eff_day,
        e.rec_day,
        [Line("1500", debit=e.amount), Line("3000", credit=e.amount - 1), Line("2700", credit=1)],
        e.amount,
        e.counterparty,
        e.doc_id,
    )
    with pytest.raises(ValueError, match="two-line"):
        inject(multi, {"inflate": 0.1})
