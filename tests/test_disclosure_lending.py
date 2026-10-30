import math

import numpy as np
import pytest
from conftest import SMALL

from veriledger.audit import AuditResult
from veriledger.disclosure import attested, insider_path, periodic, public, run_regime
from veriledger.lending import lend
from veriledger.metrics import evaluate
from veriledger.misstate import inject
from veriledger.synth import EconomyConfig, generate


def test_evaluate_counts(misstated):
    nothing = evaluate(misstated, AuditResult("x", 0))
    assert nothing["n_detected"] == 0
    assert nothing["residual_overstatement"] == pytest.approx(nothing["amount_misstated"])
    every = AuditResult(
        "y",
        0,
        detected={e.entry_id for e in misstated.misstated()},
        corrected={e.entry_id for e in misstated.misstated()},
    )
    ev = evaluate(misstated, every)
    assert ev["recall"] == 1.0 and ev["residual_overstatement"] == 0.0


def test_evaluate_without_misstatements(econ):
    ev = evaluate(econ, AuditResult("x", 0))
    assert math.isnan(ev["recall"]) and math.isnan(ev["recall_fictitious"])


def test_insider_path_is_cumulative_truth(misstated):
    path = insider_path(misstated)
    assert np.all(np.diff(path) >= 0)
    truth = sum(e.true_amount for e in misstated.entries if e.kind == "sale" and e.true_day <= misstated.fiscal_end)
    assert path[-1] == pytest.approx(truth)


def test_regime_ordering(misstated):
    p, u, a = periodic(misstated, rng=0), public(misstated), attested(misstated, rng=0)
    assert p.staleness > 40 > u.staleness and a.staleness == pytest.approx(u.staleness + 1)
    assert p.gap_index > a.gap_index
    assert (u.counterparty_exposure, a.counterparty_exposure, p.counterparty_exposure) == (1.0, 0.0, 0.0)
    assert np.isnan(p.outsider[: p.window_start]).all() and not np.isnan(p.outsider[p.window_start :]).any()
    assert a.audit["recall"] >= u.audit["recall"]
    assert set(p.summary()) >= {"gap_index", "staleness_days", "counterparty_exposure"}


def test_public_ledger_without_misstatement_tracks_truth(econ):
    r = public(econ)
    assert r.gap_index < 0.01  # only the posting lag separates outsiders from insiders
    assert r.audit["n_misstated"] == 0


def test_attested_improves_with_adoption():
    gaps = []
    for a in (0.0, 1.0):
        e = inject(generate(EconomyConfig(**SMALL, adoption=a), seed=3), {"fictitious": 0.05, "inflate": 0.05}, seed=4)
        gaps.append(attested(e, rng=0).audit["residual_overstatement"])
    assert gaps[1] < gaps[0]


def test_run_regime_dispatch(econ):
    assert run_regime("public", econ).regime == "public"
    with pytest.raises(ValueError):
        run_regime("nope", econ)


def test_lending_policies():
    econ = generate(EconomyConfig(**SMALL, adoption=1.0, record_lag_probs=(1.0,), mirror_noise=0.0), seed=5)
    clean = {p: lend(econ, p, rng=0) for p in ("trust", "haircut", "field_exam", "ledger")}
    assert clean["trust"].over_advance == pytest.approx(0.0, abs=1e-6)
    assert clean["haircut"].shortfall > 0
    m = inject(econ, {"fictitious": 0.05, "inflate": 0.05, "target": "purchase"}, seed=6)
    padded = {p: lend(m, p, rng=0) for p in ("trust", "haircut", "field_exam", "ledger")}
    assert padded["trust"].over_advance > 0
    assert padded["ledger"].over_advance <= padded["field_exam"].over_advance <= padded["trust"].over_advance
    assert padded["ledger"].verified_share > 0.9
    assert padded["ledger"].cost_hours < padded["field_exam"].cost_hours
    assert set(padded["ledger"].summary()) >= {"credit", "over_advance", "shortfall"}
    with pytest.raises(ValueError):
        lend(m, "nope")


def test_public_ledger_shows_records_as_posted(misstated):
    """Tampering after the period closes cannot change what a real-time ledger already published."""
    r = public(misstated)
    assert r.audit["n_tamper"] > 0 and r.audit["recall_tamper"] == 1.0
    assert set(misstated.tampering) <= {e.entry_id for e in misstated.entries}
    # the outsider path is built from the original amounts
    pub = sum(e.amount for e in misstated.entries if e.kind == "sale" and e.misstatement != "duplicate")
    assert r.outsider[-1] <= pub + 1e-6


def test_public_ledger_neutralises_backdating_by_timestamps(misstated):
    r = public(misstated)
    assert r.audit["n_backdate"] > 0 and r.audit["recall_backdate"] == 1.0
    # nothing else is caught except duplicates
    for s in ("fictitious", "inflate", "round_trip"):
        assert r.audit[f"n_{s}"] == 0 or r.audit[f"recall_{s}"] == 0.0


def test_attested_aggregates_use_published_amounts(misstated):
    r = attested(misstated, rng=1)
    assert r.audit["recall_tamper"] == 1.0


def test_lending_ledger_counts_cleared_flags_as_verified():
    econ = generate(EconomyConfig(adoption=1.0, days=120, periods=4, record_lag_probs=(1.0,), mirror_noise=0.0), seed=5)
    econ = inject(econ, {"fictitious": 0.03, "inflate": 0.03, "target": "purchase"}, seed=6)
    r = lend(econ, "ledger")
    # with every supplier on the ledger only the detected misstatements remain unverified
    assert r.shortfall < 1e-6


def test_pseudonymous_public_ledger(misstated):
    named, pseudo = public(misstated), public(misstated, pseudonymize=True)
    assert named.counterparty_exposure == pytest.approx(1.0) and pseudo.counterparty_exposure == 0.0
    assert pseudo.volume_linkability == pytest.approx(1.0)
    assert named.gap_index == pseudo.gap_index


def test_attestation_lag_delays_disclosure(misstated):
    a0, a3 = attested(misstated, rng=1, attest_lag=0), attested(misstated, rng=1, attest_lag=3)
    assert a3.staleness == pytest.approx(a0.staleness + 3)
    # (the gap index need not rise: a delay can offset undetected overstatement)
