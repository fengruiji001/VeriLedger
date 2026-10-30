import math

import numpy as np
import pytest
from conftest import ALL_SCHEMES, SMALL

from veriledger.audit import (
    CostModel,
    CrossLedgerAudit,
    DetectionModel,
    LedgerAudit,
    SamplingAudit,
    attribute_sample_size,
    build_ledger,
    make_regime,
    mus_sample_size,
    mus_select,
    reliability_factor,
)
from veriledger.metrics import evaluate
from veriledger.misstate import inject
from veriledger.psi import get_backend, have_x25519, intersect
from veriledger.synth import EconomyConfig, generate


def test_sample_size_formulas():
    assert reliability_factor(0.95) == pytest.approx(2.9957, abs=1e-4)
    assert mus_sample_size(1_000_000, 30_000, 0.95) == math.ceil(1_000_000 * 2.995732 / 30_000)
    assert mus_sample_size(0, 10) == 0
    assert attribute_sample_size(0.95, 0.05) == 59
    with pytest.raises(ValueError):
        mus_sample_size(100, 1, expected=1)
    with pytest.raises(ValueError):
        reliability_factor(1.0)
    with pytest.raises(ValueError):
        attribute_sample_size(0.95, 0.0)


def test_mus_selection_probability_proportional_to_size():
    amounts = np.array([1.0, 2.0, 7.0, 40.0, 50.0])
    rng = np.random.default_rng(0)
    hits = np.zeros(5)
    reps = 20000
    for _ in range(reps):
        hits[mus_select(amounts, 4, rng)] += 1
    expected = np.minimum(1.0, amounts * 4 / amounts.sum())
    assert np.allclose(hits / reps, expected, atol=0.01)
    assert mus_select(amounts, 0, rng).size == 0


def test_sampling_detection_matches_analytic_probability(small_cfg):
    """One fictitious sale of known size: detection frequency equals P(selected) * P(vouch)."""
    econ = generate(small_cfg, seed=4)
    m = inject(econ, {"fictitious": 0.0}, seed=0)
    target = m.year_entries("sale")[10]
    target.misstatement = "inflate"  # label only, amount unchanged: isolates the sampling probability
    audit = SamplingAudit(cutoff_per_boundary=0)
    pop = m.year_entries("sale")  # the audit covers the fiscal year
    bv = sum(e.amount for e in pop)
    n = audit.sample_size(bv, len(pop))
    p_select = min(1.0, target.amount * n / bv)
    rng = np.random.default_rng(1)
    reps = 3000
    det = sum(target.entry_id in audit.run(m, rng=rng).detected for _ in range(reps))
    assert det / reps == pytest.approx(p_select * 0.9, abs=0.03)


def test_attribute_and_random_methods(misstated):
    for method in ("attribute", "random"):
        r = SamplingAudit(method=method, tolerable=0.05, cutoff_per_boundary=0).run(misstated, rng=0)
        assert r.sampled == 59
    with pytest.raises(ValueError):
        SamplingAudit(method="nope").run(misstated)


def test_ledger_audit_finds_backdate_duplicate_tamper(misstated):
    res = LedgerAudit().run(misstated, rng=0)
    ev = evaluate(misstated, res)
    for s in ("backdate", "duplicate", "tamper"):
        assert ev[f"recall_{s}"] == 1.0
    assert ev["recall_round_trip"] == 0.0
    assert res.cost_breakdown["ledger"] > 0 and res.false_positives >= 0


def test_ledger_checks_can_be_switched_off(misstated):
    res = LedgerAudit(integrity=False, cutoff=False, duplicates=False).run(misstated, rng=0)
    assert not res.detected


def test_crossledger_full_adoption_detects_all_but_round_trips():
    econ = generate(EconomyConfig(**SMALL, adoption=1.0), seed=21)
    m = inject(econ, ALL_SCHEMES, seed=22)
    res = CrossLedgerAudit(residual_sampling=False).run(m, rng=0)
    ev = evaluate(m, res)
    for s in ("backdate", "fictitious", "inflate", "duplicate", "tamper"):
        assert ev[f"recall_{s}"] == 1.0, s
    assert ev["recall_round_trip"] == 0.0  # colluding related party confirms the round trip
    assert len(res.checked) == len(m.year_entries("sale"))
    assert res.matched == len(res.confirmed) < len(res.checked)


def test_crossledger_zero_adoption_falls_back_to_sampling():
    econ = generate(EconomyConfig(**SMALL, adoption=0.0, related_parties=0), seed=21)
    m = inject(econ, {**ALL_SCHEMES, "round_trip": 0}, seed=22)
    res = CrossLedgerAudit().run(m, rng=0)
    assert res.matched == 0 and res.sampled > 0 and "residual sampling" in res.cost_breakdown


def test_mirror_noise_creates_false_positives():
    econ = generate(EconomyConfig(**SMALL, adoption=1.0, mirror_noise=0.2), seed=2)
    res = CrossLedgerAudit(residual_sampling=False, ledger_checks=False).run(econ, rng=0)
    assert res.false_positives > 0 and not res.detected


def test_cost_falls_and_recall_rises_with_adoption():
    out = []
    for a in (0.0, 1.0):
        econ = generate(EconomyConfig(**SMALL, adoption=a), seed=8)
        m = inject(econ, ALL_SCHEMES, seed=9)
        out.append(evaluate(m, CrossLedgerAudit().run(m, rng=1)))
    assert out[1]["recall"] > out[0]["recall"]
    assert out[1].get("cost_residual_sampling", 0.0) < out[0]["cost_residual_sampling"]


def test_make_regime_and_models():
    assert isinstance(make_regime("crossledger", {"residual": {"confidence": 0.9}}), CrossLedgerAudit)
    assert make_regime("crossledger", {"residual": {"confidence": 0.9}}).residual.cutoff_per_boundary == 0
    with pytest.raises(ValueError):
        make_regime("nope")
    with pytest.raises(ValueError):
        make_regime("sampling", {"bogus": 1})
    d = DetectionModel.from_dict({"vouch": {"duplicate": 0.1}})
    assert d.vouch["duplicate"] == 0.1 and d.vouch["fictitious"] == 0.9
    with pytest.raises(ValueError):
        DetectionModel.from_dict({"x": 1})
    assert CostModel.from_dict({"per_item": 2.0}).per_item == 2.0


def test_build_ledger_applies_tampering(misstated):
    led = build_ledger(misstated)
    assert led.entry_ids(led.verify().modified) == set(misstated.tampering)


# ---------------------------------------------------------------------- PSI
BACKENDS = ["plain", "modp"] + (["x25519"] if have_x25519() else [])


@pytest.mark.parametrize("backend", BACKENDS)
def test_psi_matches_set_intersection(backend):
    rng = np.random.default_rng(0)
    a = [f"a{i}".encode() for i in rng.choice(200, size=40, replace=False)]
    b = [f"a{i}".encode() for i in rng.choice(200, size=40, replace=False)]
    member, st = intersect(a, b, backend)
    assert member == [x in set(b) for x in a]
    assert st.n_a == 40 and st.n_b == 40 and st.bytes_sent > 0
    assert st.group_ops == (0 if backend == "plain" else 160)


@pytest.mark.parametrize("backend", BACKENDS)
def test_psi_edge_cases(backend):
    assert intersect([], [b"x"], backend)[0] == []
    assert intersect([b"x", b"x"], [b"x"], backend)[0] == [True, True]
    assert intersect([b"x"], [], backend)[0] == [False]


def test_psi_backend_errors_and_auto():
    with pytest.raises(ValueError):
        get_backend("rsa")
    assert get_backend("auto").name in ("x25519", "modp")


def test_crossledger_backends_agree(misstated):
    results = {
        be: CrossLedgerAudit(psi_backend=be, residual_sampling=False).run(misstated, rng=0).detected
        for be in BACKENDS
        if be != "modp"
    }
    assert len({frozenset(v) for v in results.values()}) == 1
