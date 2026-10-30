"""Tests for the 0.2 models: year-end cut-off, MUS evaluation and opinions, interim reviews."""

import math

import numpy as np
import pytest
from conftest import ALL_SCHEMES, SMALL

from veriledger.audit import (
    AuditResult,
    CrossLedgerAudit,
    DetectionModel,
    InterimReview,
    SamplingAudit,
    mus_evaluate,
    poisson_upper,
)
from veriledger.disclosure import periodic
from veriledger.metrics import evaluate
from veriledger.misstate import annual_overstatement, inject
from veriledger.synth import EconomyConfig, generate


# ---------------------------------------------------------------- year end
def test_following_year_is_simulated_but_not_audited(econ):
    assert econ.last_day == econ.fiscal_end + 30
    after = [e for e in econ.entries if e.eff_day > econ.fiscal_end]
    assert after and all(not econ.in_year(e.eff_day) for e in after)
    assert econ.period_of(econ.fiscal_end + 1) == len(econ.period_bounds)
    res = SamplingAudit().run(econ, rng=0)
    assert res.population == len(econ.year_entries("sale"))


def test_fiscal_year_transactions_do_not_depend_on_post_days():
    def key(econ):
        return sorted(
            (e.kind, e.eff_day, e.amount, e.counterparty or "") for e in econ.entries if econ.in_year(e.eff_day)
        )

    a = generate(EconomyConfig(**SMALL, post_days=0), seed=3)
    b = generate(EconomyConfig(**SMALL, post_days=30), seed=3)
    assert key(a) == key(b)
    assert all(a.in_year(e.eff_day) for e in a.entries)


def test_backdating_crosses_the_year_end(econ):
    m = inject(econ, {"backdate": 0.5}, seed=1)
    bd = [e for e in m.entries if e.misstatement == "backdate" and e.kind == "sale"]
    moved = [e for e in m.entries if e.misstatement == "backdate" and e.kind == "cogs"]
    assert len(moved) == len(bd)  # the cost of sales moves with the invoice
    assert {(e.doc_id, e.eff_day) for e in moved} == {(e.doc_id, e.eff_day) for e in bd}
    across = [e for e in bd if e.true_day > m.fiscal_end]
    within = [e for e in bd if e.true_day <= m.fiscal_end]
    assert across and within
    assert all(m.in_year(e.eff_day) for e in bd)
    assert all(annual_overstatement(e, m.fiscal_end) == e.amount for e in across)
    assert all(annual_overstatement(e, m.fiscal_end) == 0.0 for e in within)
    ev = evaluate(m, AuditResult("none", 0))
    assert ev["amount_misstated_annual"] == pytest.approx(sum(e.amount for e in across))
    assert ev["amount_misstated"] == pytest.approx(sum(e.amount for e in bd))


def test_no_year_end_backdating_without_following_year():
    e = generate(EconomyConfig(**SMALL, post_days=0), seed=3)
    m = inject(e, {"backdate": 0.5}, seed=1)
    assert all(x.true_day <= m.fiscal_end for x in m.entries if x.misstatement == "backdate")


def test_adoption_share_counts_independent_parties_only():
    for a in (0.25, 0.5):
        e = generate(EconomyConfig(**SMALL, adoption=a), seed=8)
        ind = [p for p in e.parties.values() if p.role == "customer" and not p.related]
        assert sum(p.adopts for p in ind) == round(a * len(ind))
        assert all(p.adopts for p in e.parties.values() if p.related)


# ---------------------------------------------------------------- MUS evaluation
@pytest.mark.parametrize("k,rf", [(0, 3.00), (1, 4.75), (2, 6.30), (3, 7.76), (5, 10.52)])
def test_poisson_reliability_factors(k, rf):
    assert poisson_upper(k, 0.95) == pytest.approx(rf, abs=0.01)


def test_stringer_bound():
    ev = mus_evaluate([0.5], 0.0, 10_000.0, 0.95)
    assert ev.upper_limit == pytest.approx(
        10_000 * poisson_upper(0) + 0.5 * 10_000 * (poisson_upper(1) - poisson_upper(0))
    )
    assert ev.projected == pytest.approx(5_000.0)
    ev2 = mus_evaluate([0.2, 1.0], 3_000.0, 10_000.0, 0.95, known=500.0)
    rf = [poisson_upper(k) for k in range(3)]
    assert ev2.upper_limit == pytest.approx(10_000 * (rf[0] + 1.0 * (rf[1] - rf[0]) + 0.2 * (rf[2] - rf[1])) + 3_500)


def test_clean_books_get_a_clean_opinion(econ):
    res = SamplingAudit().run(econ, rng=0)
    ev = evaluate(econ, res)
    assert res.opinion == "clean" and res.upper_limit <= res.tolerable_amount
    assert ev["clean_opinion"] == 1.0 and ev["audit_failure"] == 0.0


def test_heavy_misstatement_leads_to_modified_opinions(small_cfg):
    mods = 0
    for s in range(10):
        e = inject(generate(small_cfg, seed=s), {"fictitious": 0.15, "inflate": 0.2}, seed=100 + s)
        mods += SamplingAudit().run(e, rng=s).opinion == "modified"
    assert mods >= 8


def test_regimes_without_a_sample_give_no_opinion(misstated):
    res = CrossLedgerAudit(residual_sampling=False).run(misstated, rng=0)
    ev = evaluate(misstated, res)
    assert res.opinion == "none" and math.isnan(ev["clean_opinion"]) and math.isnan(ev["audit_failure"])


def test_full_adoption_crossledger_opinion():
    e = generate(EconomyConfig(**SMALL, adoption=1.0), seed=4)
    res = CrossLedgerAudit().run(e, rng=0)
    assert res.opinion == "clean" and res.upper_limit == 0.0


# ---------------------------------------------------------------- interim review
def test_review_detection_and_cost(misstated):
    det = DetectionModel.from_dict({"review": {s: 1.0 for s in ALL_SCHEMES}})
    ids = {e.entry_id for e in misstated.year_entries("sale")}
    r = InterimReview().run(misstated, rng=0, detection=det, subset=ids)
    assert r.detected == {e.entry_id for e in misstated.year_entries("sale") if e.misstatement}
    assert r.cost == pytest.approx(16.0)


def test_periodic_reviews_then_annual_audit(misstated):
    none = DetectionModel.from_dict({"review": {s: 0.0 for s in ALL_SCHEMES}, "vouch": {s: 0.0 for s in ALL_SCHEMES}})
    audit_only = DetectionModel.from_dict(
        {"review": {s: 0.0 for s in ALL_SCHEMES}, "vouch": {s: 1.0 for s in ALL_SCHEMES}}
    )
    a = periodic(misstated, rng=0, detection=none)
    b = periodic(misstated, rng=0, detection=audit_only)
    n = len(misstated.period_bounds)
    assert a.audit["cost_review"] == pytest.approx(16.0 * (n - 1))
    # the annual audit corrects only the annual report, which is published after the fiscal year
    assert np.allclose(a.outsider, b.outsider, equal_nan=True) and a.gap_index == b.gap_index
    assert b.audit["residual_overstatement"] < a.audit["residual_overstatement"]
    assert b.audit["opinion"] in ("clean", "modified")
