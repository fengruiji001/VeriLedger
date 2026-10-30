"""Verification regimes: statistical sampling, ledger-based full verification and cross-ledger matching.

All regimes audit one *population* (by default the focal firm's sales). Misstatement labels are used
only to decide whether a procedure that touches an entry detects it, with the probabilities in
:class:`DetectionModel`. Costs are in auditor hours under :class:`CostModel`. Default parameter values
are illustrative assumptions, not empirical estimates; change them to match the setting studied.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, fields
from typing import Any

import numpy as np

from .ledger import Ledger
from .misstate import SCHEMES, annual_overstatement
from .model import Economy, Entry
from .psi import PSIStats, get_backend, intersect
from .validation import finite, has_document_id, integer


def _from_dict(cls, d):
    d = dict(d or {})
    known = {f.name for f in fields(cls)}
    unknown = set(d) - known
    if unknown:
        raise ValueError(f"unknown {cls.__name__} parameters: {sorted(unknown)}")
    return cls(**d)


@dataclass
class DetectionModel:
    """Probability that a procedure detects a misstatement once it examines the entry.

    ``vouch``: inspecting a sampled entry against documents or confirmations. ``investigate``:
    following up an entry that an automated check has flagged. ``review``: the review of an interim
    report, per misstated entry of the period.
    """

    vouch: dict[str, float] = field(
        default_factory=lambda: {
            "backdate": 0.9,
            "fictitious": 0.9,
            "inflate": 0.9,
            "duplicate": 0.5,
            "round_trip": 0.3,
            "tamper": 0.9,
        }
    )
    investigate: dict[str, float] = field(
        default_factory=lambda: {
            "backdate": 1.0,
            "fictitious": 1.0,
            "inflate": 1.0,
            "duplicate": 1.0,
            "round_trip": 0.3,  # the colluding related party supplies consistent documents
            "tamper": 1.0,
        }
    )
    #: review of an interim report (analytical procedures and inquiry, limited assurance): probability
    #: that a misstated entry of the reviewed period comes to light
    review: dict[str, float] = field(
        default_factory=lambda: {
            "backdate": 0.05,
            "fictitious": 0.05,
            "inflate": 0.05,
            "duplicate": 0.05,
            "round_trip": 0.0,
            "tamper": 0.0,
        }
    )

    def __post_init__(self) -> None:
        for key in ("vouch", "investigate", "review"):
            probs = getattr(self, key)
            unknown = set(probs) - set(SCHEMES)
            if unknown:
                raise ValueError(f"unknown misstatement schemes in {key}: {sorted(unknown)}")
            for probability in probs.values():
                finite(key, probability, 0.0, 1.0)
            if any(not 0.0 <= float(p) <= 1.0 for p in probs.values()):
                raise ValueError(f"{key} probabilities must be in [0, 1]")

    @classmethod
    def from_dict(cls, d: dict | None) -> DetectionModel:
        base = cls()
        d = dict(d or {})
        for key in ("vouch", "investigate", "review"):
            if key in d:
                getattr(base, key).update(d.pop(key))
        if d:
            raise ValueError(f"unknown DetectionModel parameters: {sorted(d)}")
        base.__post_init__()
        return base


@dataclass
class CostModel:
    """Auditor hours per procedure (illustrative defaults)."""

    sampling_fixed: float = 8.0
    per_item: float = 1.0
    ledger_setup: float = 24.0
    per_entry_auto: float = 0.0005
    per_investigation: float = 0.5
    psi_setup: float = 8.0
    per_psi_element: float = 0.0002
    review_per_period: float = 16.0

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            finite(key, value)
        if any(v < 0 for v in asdict(self).values()):
            raise ValueError("audit hours must not be negative")

    @classmethod
    def from_dict(cls, d: dict | None) -> CostModel:
        return _from_dict(cls, d)


@dataclass
class AuditResult:
    regime: str
    population: int
    detected: set[str] = field(default_factory=set)
    flagged: set[str] = field(default_factory=set)
    corrected: set[str] = field(default_factory=set)
    confirmed: set[str] = field(default_factory=set)
    cleared: set[str] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    investigated: int = 0
    false_positives: int = 0
    sampled: int = 0
    matched: int = 0
    checked: set[str] = field(default_factory=set)  # entries matched against a counterparty's records
    cost: float = 0.0
    cost_breakdown: dict[str, float] = field(default_factory=dict)
    psi: list[PSIStats] = field(default_factory=list)
    # evaluation of a monetary-unit sample (NaN when the regime draws none)
    tolerable_amount: float = float("nan")
    projected_misstatement: float = float("nan")
    upper_limit: float = float("nan")
    known_misstatement: float = float("nan")
    corrected_misstatement: float = float("nan")  # misstatement found and corrected before the evaluation
    opinion: str = "none"  # "clean" (upper limit within tolerable misstatement), "modified" or "none"

    def add_cost(self, item: str, hours: float) -> None:
        self.cost_breakdown[item] = self.cost_breakdown.get(item, 0.0) + hours
        self.cost += hours


# ---------------------------------------------------------------------- sampling formulas
def reliability_factor(confidence: float) -> float:
    """Poisson reliability factor for zero expected errors, ``-ln(1 - confidence)``."""
    finite("confidence", confidence)
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    return -math.log1p(-confidence)


def mus_sample_size(
    book_value: float, tolerable: float, confidence: float = 0.95, expected: float = 0.0, expansion: float = 1.6
) -> int:
    """Monetary-unit sample size ``ceil(BV * RF / (TM - EM * EF))`` (amounts in currency units)."""
    for name, value in (
        ("book_value", book_value),
        ("tolerable", tolerable),
        ("expected", expected),
        ("expansion", expansion),
    ):
        finite(name, value, 0.0)
    rf = reliability_factor(confidence)
    if book_value <= 0:
        return 0
    denom = tolerable - expected * expansion
    if denom <= 0:
        raise ValueError("tolerable misstatement must exceed expected misstatement times the expansion factor")
    required = book_value * rf / denom
    finite("calculated sample size", required, 0.0)
    return max(1, int(math.ceil(required)))


def attribute_sample_size(confidence: float = 0.95, tolerable_rate: float = 0.05) -> int:
    """Discovery sample size with zero expected deviations, ``ceil(ln(1 - CL) / ln(1 - TR))``."""
    if not 0 < confidence < 1 or not 0 < tolerable_rate < 1:
        raise ValueError("confidence and tolerable_rate must be in (0, 1)")
    return int(math.ceil(math.log1p(-confidence) / math.log1p(-tolerable_rate)))


def poisson_upper(k: int, confidence: float = 0.95) -> float:
    """Upper confidence limit of a Poisson mean after observing ``k`` events (the MUS reliability
    factor for ``k`` misstatements); ``poisson_upper(0, CL) = -ln(1 - CL)``."""
    integer("k", k)
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    if k == 0:
        return reliability_factor(confidence)
    target = 1.0 - confidence

    def cdf(lam: float) -> float:
        if lam <= k:
            # Backward summation below the mode can overflow while P(X=k)
            # underflows. The complementary upper tail decreases from its first term.
            return 1.0 - upper_tail(lam)
        # Summing forward from P(X=0) underflows for large means.  Start at
        # P(X=k), calculated in log space, and sum the lower tail backwards.
        term = total = 1.0
        for i in range(k, 0, -1):
            term *= i / lam
            total += term
            if term <= 1e-16 * total:
                break
        return math.exp(k * math.log(lam) - lam - math.lgamma(k + 1)) * total

    def upper_tail(lam: float) -> float:
        # For low confidence the root can be near zero, where 1 - CDF
        # loses precision. Sum P(X > k) directly from P(X = k + 1).
        term = total = 1.0
        j = k + 1
        while True:
            j += 1
            term *= lam / j
            total += term
            if term <= 1e-16 * total:
                break
        return math.exp((k + 1) * math.log(lam) - lam - math.lgamma(k + 2)) * total

    lo, hi = 0.0, float(k) + 10.0 * math.sqrt(k + 1) + 10.0
    # High-confidence bounds can exceed the former fixed upper bracket.
    while cdf(hi) > target:
        hi *= 2.0
    for _ in range(2048):
        mid = 0.5 * (lo + hi)
        below = upper_tail(mid) < confidence if confidence <= 0.5 else cdf(mid) > target
        if below:
            lo = mid
        else:
            hi = mid
        if hi - lo <= max(mid * 1e-13, 1e-300):
            break
    return 0.5 * (lo + hi)


@dataclass
class MUSEvaluation:
    """Evaluation of a monetary-unit sample with the Stringer bound."""

    interval: float
    basic_precision: float
    projected: float  # projected misstatement of the items below the interval plus top-stratum misstatement
    upper_limit: float
    known: float  # misstatement found in items at or above the interval and outside the statistical sample


def mus_evaluate(
    taintings: list[float], top_stratum: float, interval: float, confidence: float = 0.95, known: float = 0.0
) -> MUSEvaluation:
    """Stringer upper misstatement limit.

    ``taintings`` are the misstatement-to-book-value ratios found in sampled items below the sampling
    interval; ``top_stratum`` is the misstatement found in items at or above it (counted in full);
    ``known`` is misstatement found outside the statistical sample (for example in a cut-off test).
    ``UML = J RF(0) + J sum_k t_(k) (RF(k) - RF(k-1)) + top_stratum + known`` with taintings in
    decreasing order and ``RF(k)`` the Poisson upper limit for ``k`` misstatements.
    """
    for name, value in (("top_stratum", top_stratum), ("interval", interval), ("known", known)):
        finite(name, value, 0.0)
    for taint in taintings:
        finite("tainting", taint, 0.0, 1.0)
    t = sorted(taintings, reverse=True)
    rf = [poisson_upper(k, confidence) for k in range(len(t) + 1)]
    basic = interval * rf[0]
    allowance = interval * sum(tk * (rf[k + 1] - rf[k]) for k, tk in enumerate(t))
    projected = interval * sum(t) + top_stratum
    return MUSEvaluation(interval, basic, projected, basic + allowance + top_stratum + known, top_stratum + known)


def mus_select(amounts: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """Systematic probability-proportional-to-size selection; returns unique selected indices."""
    integer("sample size", n)
    amounts = np.asarray(amounts)
    if amounts.ndim != 1 or amounts.dtype.kind not in "iuf":
        raise ValueError("amounts must be a one-dimensional numeric population")
    amounts = amounts.astype(float)
    if np.any(~np.isfinite(amounts)) or np.any(amounts < 0):
        raise ValueError("amounts must be finite and non-negative")
    if n <= 0 or amounts.size == 0:
        return np.array([], dtype=int)
    with np.errstate(over="ignore", invalid="ignore"):
        total = float(amounts.sum())
    finite("population total", total, 0.0)
    if total == 0:
        return np.array([], dtype=int)
    interval = total / n
    points = rng.uniform(0, interval) + interval * np.arange(n)
    cum = np.cumsum(amounts)
    idx = np.searchsorted(cum, points, side="right")
    return np.unique(np.clip(idx, 0, amounts.size - 1))


# ---------------------------------------------------------------------- helpers
def population(econ: Economy, kind: str = "sale", apparent: bool = True) -> list[Entry]:
    """Entries of ``kind`` dated in the fiscal year, as the firm's database shows them (``apparent``)."""
    return econ.year_entries(kind, apparent=apparent)


def _examine(entries: list[Entry], probs: dict[str, float], rng: np.random.Generator, res: AuditResult) -> None:
    for e in entries:
        if e.misstatement and rng.random() < probs.get(e.misstatement, 0.0):
            res.detected.add(e.entry_id)


def _investigate(flagged: list[Entry], det: DetectionModel, rng: np.random.Generator, res: AuditResult) -> None:
    for e in flagged:
        res.flagged.add(e.entry_id)
        res.investigated += 1
        if e.misstatement:
            if rng.random() < det.investigate.get(e.misstatement, 0.0):
                res.detected.add(e.entry_id)
            else:
                res.unresolved.add(e.entry_id)
        else:
            res.false_positives += 1
            res.cleared.add(e.entry_id)


# ---------------------------------------------------------------------- regimes
@dataclass
class SamplingAudit:
    """Conventional audit: monetary-unit (or random) sampling plus a cut-off test around period ends."""

    method: str = "mus"
    confidence: float = 0.95
    tolerable: float = 0.03  # MUS: share of book value; attribute/random: tolerable deviation rate
    expected: float = 0.0  # MUS: expected misstatement as a share of book value
    expansion: float = 1.6
    #: management corrects the misstatements the auditor finds, and the upper limit is reduced by the
    #: corrections before it is compared with tolerable misstatement (AICPA Audit Sampling guide)
    correct: bool = True
    cutoff_window: int = 5
    cutoff_per_boundary: int = 20
    duplicate_test: bool = False

    def __post_init__(self) -> None:
        for name in ("confidence", "tolerable", "expected", "expansion"):
            finite(name, getattr(self, name), 0.0)
        integer("cutoff_window", self.cutoff_window)
        integer("cutoff_per_boundary", self.cutoff_per_boundary)
        if not isinstance(self.correct, bool) or not isinstance(self.duplicate_test, bool):
            raise ValueError("correct and duplicate_test must be booleans")
        if self.method not in ("mus", "attribute", "random"):
            raise ValueError(f"unknown sampling method {self.method!r}; use 'mus', 'attribute' or 'random'")
        if not 0 < self.confidence < 1 or not 0 < self.tolerable < 1:
            raise ValueError("confidence and tolerable must be in (0, 1)")
        if self.expected < 0 or self.expansion < 0 or self.cutoff_window < 0 or self.cutoff_per_boundary < 0:
            raise ValueError("expected, expansion and the cut-off settings must not be negative")

    @classmethod
    def from_dict(cls, d: dict | None) -> SamplingAudit:
        return _from_dict(cls, d)

    def sample_size(self, book_value: float, n_items: int, tolerable_amount: float | None = None) -> int:
        if self.method == "mus":
            tm = tolerable_amount if tolerable_amount is not None else self.tolerable * book_value
            return mus_sample_size(book_value, tm, self.confidence, self.expected * book_value, self.expansion)
        if self.method in ("attribute", "random"):
            return min(n_items, attribute_sample_size(self.confidence, self.tolerable))
        raise ValueError(f"unknown sampling method {self.method!r}")

    def run(
        self,
        econ: Economy,
        kind: str = "sale",
        rng: np.random.Generator | int = 0,
        detection: DetectionModel | None = None,
        costs: CostModel | None = None,
        subset: set[str] | None = None,
        tolerable_amount: float | None = None,
        regime: str = "sampling",
    ) -> AuditResult:
        econ.require_ground_truth()
        rng = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
        det, cm = detection or DetectionModel(), costs or CostModel()
        pop = population(econ, kind)
        if subset is not None:
            pop = [e for e in pop if e.entry_id in subset]
        res = AuditResult(regime, len(pop))
        if not pop:
            return res
        amounts = np.array([e.amount for e in pop])
        book = float(amounts.sum())
        tm = tolerable_amount if tolerable_amount is not None else self.tolerable * book
        n = self.sample_size(book, len(pop), tolerable_amount)
        if self.method == "mus":
            idx = mus_select(amounts, n, rng)
        else:
            idx = rng.choice(len(pop), size=min(n, len(pop)), replace=False)
        stat = {int(i) for i in idx}
        chosen = set(stat)
        # cut-off test: random entries dated in the last days of each period
        for _, b in econ.period_bounds:
            window = [i for i, e in enumerate(pop) if b - self.cutoff_window < e.eff_day <= b]
            if window:
                k = min(self.cutoff_per_boundary, len(window))
                chosen.update(int(window[j]) for j in rng.choice(len(window), size=k, replace=False))
        _examine([pop[i] for i in sorted(chosen)], det.vouch, rng, res)
        res.sampled = len(chosen)
        res.add_cost("sampling", cm.sampling_fixed + cm.per_item * len(chosen))
        res.tolerable_amount = tm
        if self.duplicate_test:
            seen: set[tuple[str, str | None]] = set()
            dups = []
            for e in sorted(pop, key=lambda e: (e.rec_day, e.entry_id)):
                key = (e.counterparty or "", e.doc_id)
                if has_document_id(e.doc_id) and key in seen:
                    dups.append(e)
                seen.add(key)
            _investigate(dups, det, rng, res)
            res.add_cost("analytics", cm.per_entry_auto * len(pop) + cm.per_investigation * len(dups))
        res.unresolved -= res.detected | res.cleared
        res.corrected = set(res.detected) if self.correct else set()
        if self.method == "mus" and n > 0:
            interval = book / n
            found = {
                i: annual_overstatement(e, econ.fiscal_end) for i, e in enumerate(pop) if e.entry_id in res.detected
            }
            taint = [m / pop[i].amount for i, m in found.items() if i in stat and pop[i].amount < interval]
            top = sum(m for i, m in found.items() if i in stat and pop[i].amount >= interval)
            known = sum(m for i, m in found.items() if i not in stat)
            ev = mus_evaluate(taint, top, interval, self.confidence, known)
            corrected = sum(found.values()) if self.correct else 0.0
            res.projected_misstatement, res.known_misstatement = ev.projected, ev.known
            res.corrected_misstatement = corrected
            unresolved = sum(e.amount for e in pop if e.entry_id in res.unresolved)
            res.upper_limit = max(0.0, ev.upper_limit - corrected) + unresolved
            res.opinion = "clean" if res.upper_limit <= tm else "modified"
        return res


@dataclass
class InterimReview:
    """Review of an interim report: analytical procedures and inquiry (limited assurance, no sample).

    Each misstated entry of the reviewed period comes to light with probability ``review[scheme]`` of the
    :class:`DetectionModel`; the review costs ``review_per_period`` hours."""

    @classmethod
    def from_dict(cls, d: dict | None) -> InterimReview:
        return _from_dict(cls, d)

    def run(
        self,
        econ: Economy,
        kind: str = "sale",
        rng: np.random.Generator | int = 0,
        detection: DetectionModel | None = None,
        costs: CostModel | None = None,
        subset: set[str] | None = None,
        regime: str = "review",
    ) -> AuditResult:
        econ.require_ground_truth()
        rng = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
        det, cm = detection or DetectionModel(), costs or CostModel()
        pop = [e for e in population(econ, kind) if subset is None or e.entry_id in subset]
        res = AuditResult(regime, len(pop))
        _examine(pop, det.review, rng, res)
        res.add_cost("review", cm.review_per_period)
        return res


def build_ledger(econ: Economy, close_lag: int = 5, tampered: bool = True) -> Ledger:
    """Build the firm's ledger from the records as originally posted.

    With ``tampered=True`` (the default) the firm's own copy of the records is then altered as in
    ``econ.tampering``, which is what an auditor querying the firm's records sees; the replicated hashes
    and commitments keep the original values. ``tampered=False`` gives the records as they were
    published when posted, which is what outsiders reading a real-time ledger saw."""
    led = Ledger.from_entries(econ.firm, econ.entries, econ.period_bounds, close_lag)
    if tampered:
        for eid, amount in econ.tampering.items():
            led.tamper(eid, amount=amount)
    return led


@dataclass
class LedgerAudit:
    """Full-population checks on a hash-chained ledger: integrity, cut-off timestamps, duplicates and
    entries dated after the day they were entered (``forward``; these occur in real data, not in the
    simulator)."""

    integrity: bool = True
    cutoff: bool = True
    duplicates: bool = True
    forward: bool = True
    close_lag: int = 5
    correct: bool = True

    def __post_init__(self) -> None:
        integer("close_lag", self.close_lag)
        for name in ("integrity", "cutoff", "duplicates", "forward", "correct"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")

    @classmethod
    def from_dict(cls, d: dict | None) -> LedgerAudit:
        return _from_dict(cls, d)

    def flags(self, econ: Economy, led: Ledger, kind: str) -> set[str]:
        idx: set[int] = set()
        if self.integrity:
            idx.update(led.verify().modified)
        if self.cutoff:
            idx.update(led.cutoff_flags())
        if self.duplicates:
            idx.update(led.duplicate_flags((kind,)))
        if self.forward:
            idx.update(led.forward_flags())
        return {led.records[i]["entry_id"] for i in idx if led.records[i]["kind"] == kind}

    def run(
        self,
        econ: Economy,
        kind: str = "sale",
        rng: np.random.Generator | int = 0,
        detection: DetectionModel | None = None,
        costs: CostModel | None = None,
        ledger: Ledger | None = None,
        regime: str = "ledger",
    ) -> AuditResult:
        econ.require_ground_truth()
        rng = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
        det, cm = detection or DetectionModel(), costs or CostModel()
        led = ledger or build_ledger(econ, self.close_lag)
        pop = {e.entry_id: e for e in econ.year_entries(kind)}
        res = AuditResult(regime, len(pop))
        flagged = [pop[i] for i in sorted(self.flags(econ, led, kind)) if i in pop]
        _investigate(flagged, det, rng, res)
        res.add_cost("ledger", cm.ledger_setup + cm.per_entry_auto * len(led.records))
        res.add_cost("investigation", cm.per_investigation * len(flagged))
        res.corrected = set(res.detected) if self.correct else set()
        res.corrected_misstatement = sum(
            annual_overstatement(e, econ.fiscal_end, econ.tampering.get(e.entry_id))
            for e in pop.values()
            if e.entry_id in res.corrected
        )
        return res


def fingerprint(doc_id: str, day: int, amount: float, kind: str, occurrence: int) -> bytes:
    return f"{doc_id}|{int(day)}|{amount:.2f}|{kind}|{occurrence}".encode()


def matching_key(doc_id: str, day: int, amount: float, kind: str) -> tuple[str, int, str, str]:
    """Canonical transaction key for assigning occurrences to identical records."""
    return doc_id, int(day), f"{amount:.2f}", kind


def validate_mirror_owners(econ: Economy) -> None:
    for owner, records in econ.mirrors.items():
        if any(record.owner != owner for record in records):
            raise ValueError(f"mirror owner does not match its counterparty group {owner!r}")


@dataclass
class CrossLedgerAudit:
    """Ledger checks plus simulated matching against adopting counterparties.

    Both data sets are present in process. No privacy guarantee is established; entries with
    non-adopting counterparties are sampled (``residual``).
    """

    psi_backend: str = "plain"
    ledger_checks: bool = True
    residual_sampling: bool = True
    # ledger timestamps already cover cut-off, so the residual sample has no separate cut-off test
    residual: SamplingAudit = field(default_factory=lambda: SamplingAudit(cutoff_per_boundary=0))
    close_lag: int = 5
    correct: bool = True

    def __post_init__(self) -> None:
        integer("close_lag", self.close_lag)
        if self.psi_backend not in ("plain", "x25519", "modp", "auto"):
            raise ValueError("unknown PSI backend")
        for name in ("ledger_checks", "residual_sampling", "correct"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if not isinstance(self.residual, SamplingAudit):
            raise ValueError("residual must be a SamplingAudit")

    @classmethod
    def from_dict(cls, d: dict | None) -> CrossLedgerAudit:
        d = dict(d or {})
        residual = SamplingAudit.from_dict({"cutoff_per_boundary": 0, **(d.pop("residual", None) or {})})
        obj = _from_dict(cls, d)
        obj.residual = residual
        return obj

    def match(self, econ: Economy, led: Ledger, kind: str) -> tuple[set[str], set[str], list[PSIStats]]:
        """Return (checked entry ids, unmatched entry ids, PSI statistics) for adopting counterparties."""
        validate_mirror_owners(econ)
        backend = get_backend(self.psi_backend)
        adopters = econ.adopters()
        firm_side: dict[str, list[dict]] = defaultdict(list)
        # the firm's side is read from the firm's current copy of the ledger records (after any tampering),
        # so an altered amount no longer matches the counterparty's invoice
        for rec in led.records:
            if rec["kind"] == kind and rec["counterparty"] in adopters and econ.in_year(rec["eff_day"]):
                firm_side[rec["counterparty"]].append(rec)
        checked, unmatched, stats = set(), set(), []
        for cp, recs in sorted(firm_side.items()):
            occ: Counter = Counter()
            valid_recs = []
            fa = []
            for r in recs:  # ledger order = recording order
                checked.add(r["entry_id"])
                if not has_document_id(r["doc_id"]):
                    unmatched.add(r["entry_id"])
                    continue
                valid_recs.append(r)
                key = matching_key(r["doc_id"], r["eff_day"], r["amount"], kind)
                occ[key] += 1
                fa.append(fingerprint(r["doc_id"], r["eff_day"], r["amount"], kind, occ[key]))
            occ_b: Counter = Counter()
            fb = []
            for m in econ.mirrors.get(cp, []):
                if m.kind == kind and has_document_id(m.doc_id):
                    key = matching_key(m.doc_id, m.day, m.amount, kind)
                    occ_b[key] += 1
                    fb.append(fingerprint(m.doc_id, m.day, m.amount, kind, occ_b[key]))
            if not valid_recs:
                continue
            member, st = intersect(fa, fb, backend)
            stats.append(st)
            for r, ok in zip(valid_recs, member):
                if not ok:
                    unmatched.add(r["entry_id"])
        return checked, unmatched, stats

    def run(
        self,
        econ: Economy,
        kind: str = "sale",
        rng: np.random.Generator | int = 0,
        detection: DetectionModel | None = None,
        costs: CostModel | None = None,
        ledger: Ledger | None = None,
        regime: str = "crossledger",
    ) -> AuditResult:
        econ.require_ground_truth()
        rng = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
        det, cm = detection or DetectionModel(), costs or CostModel()
        led = ledger or build_ledger(econ, self.close_lag)
        pop = {e.entry_id: e for e in econ.year_entries(kind)}
        res = AuditResult(regime, len(pop))
        flag_ids = LedgerAudit(close_lag=self.close_lag).flags(econ, led, kind) if self.ledger_checks else set()
        checked, unmatched, stats = self.match(econ, led, kind)
        res.confirmed = checked - unmatched
        res.matched, res.psi, res.checked = len(res.confirmed), stats, checked
        flag_ids = (flag_ids | unmatched) & set(pop)
        _investigate([pop[i] for i in sorted(flag_ids)], det, rng, res)
        if self.ledger_checks:
            res.add_cost("ledger", cm.ledger_setup + cm.per_entry_auto * len(led.records))
        elements = sum(s.n_a + s.n_b for s in stats)
        res.add_cost("psi", (cm.psi_setup if stats else 0.0) + cm.per_psi_element * elements)
        res.add_cost("investigation", cm.per_investigation * len(flag_ids))
        initial_found = set(res.detected)
        res.corrected = initial_found.copy() if self.correct else set()
        # Investigated known errors are removed from the statistical remainder and added explicitly.
        rest = set(pop) - checked - initial_found
        tm = self.residual.tolerable * sum(e.amount for e in population(econ, kind))
        upper = projected = 0.0
        residual_unresolved: set[str] = set()
        residual_known = 0.0
        if self.residual_sampling and rest:
            from dataclasses import replace

            sample = replace(self.residual, correct=self.correct and self.residual.correct)
            sub = sample.run(econ, kind, rng, det, cm, subset=rest, tolerable_amount=tm)
            res.detected |= sub.detected
            res.corrected |= sub.corrected
            res.flagged |= sub.flagged
            res.cleared |= sub.cleared
            res.investigated += sub.investigated
            res.false_positives += sub.false_positives
            res.unresolved |= sub.unresolved
            residual_unresolved = sub.unresolved
            if math.isfinite(sub.known_misstatement):
                residual_known = sub.known_misstatement
            res.sampled = sub.sampled
            res.add_cost("residual sampling", sub.cost)
            projected, upper = sub.projected_misstatement, sub.upper_limit
        # A later procedure can resolve an earlier exception, even when management does not correct it.
        res.unresolved -= res.detected | res.cleared
        if self.residual_sampling:
            known = sum(annual_overstatement(pop[i], econ.fiscal_end, econ.tampering.get(i)) for i in initial_found)
            outstanding = sum(
                annual_overstatement(pop[i], econ.fiscal_end, econ.tampering.get(i))
                for i in initial_found - res.corrected
            )
            # The firm's *current* ledger can contain an altered amount.  Use
            # that observed amount, not the original simulated entry amount.
            observed = {r["entry_id"]: r["amount"] for r in led.records}
            # The nested sample's upper limit already includes its own unresolved items.
            unresolved = sum(observed.get(i, pop[i].amount) for i in res.unresolved - residual_unresolved if i in pop)
            res.tolerable_amount = tm
            res.projected_misstatement = projected + known
            res.known_misstatement = known + residual_known
            if math.isfinite(upper) and self.residual.method == "mus":
                res.upper_limit = max(0.0, upper + outstanding) + unresolved
                res.opinion = "clean" if res.upper_limit <= tm else "modified"
            # Attribute/random samples have no monetary upper limit or monetary audit opinion.
        res.corrected_misstatement = sum(
            annual_overstatement(pop[i], econ.fiscal_end, econ.tampering.get(i)) for i in res.corrected
        )
        return res


REGIMES: dict[str, Any] = {"sampling": SamplingAudit, "ledger": LedgerAudit, "crossledger": CrossLedgerAudit}


def make_regime(name: str, params: dict | None = None) -> Any:
    if name not in REGIMES:
        raise ValueError(f"unknown audit regime {name!r}; choose from {sorted(REGIMES)}")
    return REGIMES[name].from_dict(params)


def regime_params(obj) -> dict:
    return asdict(obj)
