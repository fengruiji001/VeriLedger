"""Disclosure regimes and the information gap between insiders and outsiders.

An insider knows the firm's true cumulative revenue on every day. An outsider forms an estimate from
what the regime discloses:

* ``periodic``: reviewed interim reports published ``report_lag`` days after each period end and an
  audited annual report; days not yet covered are extrapolated at the last reported daily run rate.
* ``public``: a real-time public ledger (all entries, with counterparty identities). Outsiders date
  every record by its recording day, so a backdated record that is posted after the end of the period
  it claims does not mislead them, and they drop repeated documents; they cannot verify amounts
  against counterparties.
* ``attested``: the model publishes only commitments and daily aggregates after simulated matching;
  entry details are omitted from the output, but the in-process calculation provides no privacy guarantee.

Both ledger regimes publish records when they are posted, so a later alteration of the firm's own
database (``tamper``) cannot change what outsiders saw. Periodic reports are compiled from the firm's
database after the period closes and do include such alterations.

The information-gap index is ``mean_d |O_d - I_d| / mean_d I_d`` over the evaluation window, where
``O_d`` and ``I_d`` are the outsider's estimate and the insider's value of cumulative revenue.
"""

from __future__ import annotations

from bisect import insort
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import groupby

import numpy as np

from .audit import (
    AuditResult,
    CostModel,
    CrossLedgerAudit,
    DetectionModel,
    InterimReview,
    SamplingAudit,
    _investigate,
    build_ledger,
    fingerprint,
    has_document_id,
    matching_key,
    validate_mirror_owners,
)
from .metrics import evaluate
from .model import CounterRecord, Economy, with_amount
from .psi import get_backend, intersect
from .validation import integer

REGIME_NAMES = ("periodic", "public", "attested")


@dataclass
class DisclosureResult:
    regime: str
    days: np.ndarray
    insider: np.ndarray
    outsider: np.ndarray
    window_start: int
    gap_index: float
    staleness: float
    counterparty_exposure: float  # share of revenue whose counterparty is published by name
    entry_visibility: float  # share of entries that outsiders can see individually
    audit: dict = field(default_factory=dict)
    volume_linkability: float = 0.0  # share of revenue that outsiders can group by counterparty
    annual_reported: float = float("nan")
    annual_publish_day: float = float("nan")

    def summary(self) -> dict:
        return {
            "regime": self.regime,
            "annual_reported": self.annual_reported,
            "annual_publish_day": self.annual_publish_day,
            "gap_index": self.gap_index,
            "staleness_days": self.staleness,
            "counterparty_exposure": self.counterparty_exposure,
            "volume_linkability": self.volume_linkability,
            "entry_visibility": self.entry_visibility,
            "residual_overstatement": self.audit.get("residual_overstatement", float("nan")),
            "residual_overstatement_annual": self.audit.get("residual_overstatement_annual", float("nan")),
            "recall": self.audit.get("recall", float("nan")),
            "clean_opinion": self.audit.get("clean_opinion", float("nan")),
            "audit_failure": self.audit.get("audit_failure", float("nan")),
            "false_modification": self.audit.get("false_modification", float("nan")),
            "cost_hours": self.audit.get("cost_hours", 0.0),
        }


def insider_path(econ: Economy, kind: str = "sale", horizon: int | None = None) -> np.ndarray:
    econ.require_ground_truth()
    horizon = horizon or (econ.period_bounds[-1][1] + 1)
    daily = np.zeros(horizon)
    for e in econ.entries:
        if e.kind == kind and e.true_amount and 0 <= e.true_day < horizon:
            daily[e.true_day] += e.true_amount
    return np.cumsum(daily)


def _gap(insider: np.ndarray, outsider: np.ndarray, start: int) -> float:
    i, o = insider[start:], outsider[start:]
    return float(np.mean(np.abs(o - i)) / np.mean(i)) if i.size and np.mean(i) > 0 else float("nan")


def _weighted_delay(pairs: list[tuple[float, float]]) -> float:
    w = sum(a for a, _ in pairs)
    return float(sum(a * d for a, d in pairs) / w) if w else float("nan")


def periodic(
    econ: Economy,
    audit: SamplingAudit | None = None,
    report_lag: int = 45,
    rng: np.random.Generator | int = 0,
    detection: DetectionModel | None = None,
    costs: CostModel | None = None,
    kind: str = "sale",
    review: InterimReview | None = None,
    annual_lag: int | None = None,
) -> DisclosureResult:
    """Reviewed interim reports and an audited annual report.

    After each period but the last, an interim report is published ``report_lag`` days after the period
    end, following a review (:class:`InterimReview`, limited assurance). The annual report, which also
    gives the last period, is published ``annual_lag`` days (default ``report_lag``) after the year end,
    following a sampling audit of the whole year at annual materiality. Misstatements found by a review
    are corrected in that interim report; those found by the audit only in the annual report. The
    information gap is measured within the fiscal year, from the first interim report onward.
    """
    econ.require_ground_truth()
    integer("report_lag", report_lag)
    annual_lag = report_lag if annual_lag is None else annual_lag
    integer("annual_lag", annual_lag)
    rng = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
    audit = audit or SamplingAudit()
    review = review or InterimReview()
    bounds, horizon = econ.period_bounds, econ.fiscal_end + 1
    publish = [b + report_lag for _, b in bounds[:-1]] + [econ.fiscal_end + annual_lag]
    if publish != sorted(publish):
        raise ValueError("annual report must not precede an interim report")
    apparent = econ.year_entries(kind, apparent=True)
    total = AuditResult("periodic", len(apparent))
    reviewed: set[str] = set()
    snapshots: list[tuple[int, int, float, float]] = []

    def amounts(snapshot: Economy, end: int, corrected: set[str]) -> tuple[float, float]:
        cumulative = period = 0.0
        start = next(a for a, b in bounds if b == end)
        for e in snapshot.apparent_entries():
            if e.kind != kind:
                continue
            day = e.true_day if e.entry_id in corrected else e.eff_day
            amount = e.true_amount if e.entry_id in corrected else e.amount
            if 0 <= day <= end:
                cumulative += amount
                if day >= start:
                    period += amount
        return cumulative, period

    for k, (_, b) in enumerate(bounds[:-1]):
        snap = econ.as_of(publish[k])
        ids = {e.entry_id for e in snap.year_entries(kind) if econ.period_of(e.eff_day) == k}
        r = review.run(snap, kind, rng, detection, costs, subset=ids, regime="review")
        reviewed |= r.detected
        total.detected |= r.detected
        total.add_cost("review", r.cost)
        cumulative, period = amounts(snap, b, reviewed)
        snapshots.append((publish[k], k, cumulative, period))

    annual = econ.as_of(publish[-1])
    # The annual sample examines the books after corrections already made in interim reports.
    for i, e in enumerate(annual.entries):
        if e.entry_id in reviewed:
            annual.entries[i] = with_amount(e, e.true_amount).copy(eff_day=e.true_day, misstatement=None)
            annual.tampering.pop(e.entry_id, None)
    a = audit.run(annual, kind, rng, detection, costs, regime="annual audit")
    total.detected |= a.detected
    total.corrected = reviewed | a.corrected
    total.sampled = a.sampled
    total.unresolved = a.unresolved
    total.investigated, total.false_positives = a.investigated, a.false_positives
    total.flagged, total.cleared = a.flagged, a.cleared
    total.checked, total.confirmed, total.matched = a.checked, a.confirmed, a.matched
    total.add_cost("audit", a.cost)
    for f in ("tolerable_amount", "projected_misstatement", "upper_limit", "known_misstatement", "opinion"):
        setattr(total, f, getattr(a, f))
    annual_snapshot = econ.as_of(publish[-1])
    total.population = len(annual_snapshot.year_entries(kind))
    total.corrected_misstatement = evaluate(annual_snapshot, total, kind)["amount_corrected_annual"]
    annual_value, annual_period = amounts(annual_snapshot, econ.fiscal_end, total.corrected)
    snapshots.append((publish[-1], len(bounds) - 1, annual_value, annual_period))
    insider = insider_path(econ, kind, horizon)
    outsider = np.full(horizon, np.nan)
    for d in range(horizon):
        known = [s for s in snapshots if s[0] <= d]
        if known:
            _, k, cumulative, period = known[-1]
            a0, b0 = bounds[k]
            outsider[d] = cumulative + period / (b0 - a0 + 1) * max(0, d - b0)
    first_seen: list[tuple[float, float]] = []
    for e in econ.entries:
        if e.kind != kind or not e.true_amount or not econ.in_year(e.true_day):
            continue
        visible = [day for day, k, _, _ in snapshots if day >= e.rec_day and bounds[k][1] >= e.true_day]
        if visible:
            first_seen.append((e.true_amount, min(visible) - e.true_day))
    start = publish[0]
    return DisclosureResult(
        "periodic",
        np.arange(horizon),
        insider,
        outsider,
        start,
        _gap(insider, outsider, start),
        _weighted_delay(first_seen),
        0.0,
        0.0,
        evaluate(annual_snapshot, total, kind),
        annual_reported=annual_value,
        annual_publish_day=float(publish[-1]),
    )


def public(
    econ: Economy,
    report_lag: int = 45,
    rng: np.random.Generator | int = 0,
    kind: str = "sale",
    close_lag: int = 5,
    pseudonymize: bool = False,
) -> DisclosureResult:
    """Real-time public ledger: outsiders see every record when it is posted.

    With ``pseudonymize`` counterparties appear under stable pseudonyms instead of their names: their
    identities are no longer published, but their trading volumes can still be grouped."""
    econ.require_ground_truth()
    integer("report_lag", report_lag)
    integer("close_lag", close_lag)
    if not isinstance(pseudonymize, bool):
        raise ValueError("pseudonymize must be a boolean")
    horizon = econ.fiscal_end + 1
    led = build_ledger(econ, close_lag, tampered=False)  # the records as published when posted
    dup = led.duplicate_flags((kind,))
    dropped = {led.records[i]["entry_id"] for i in dup}
    late = led.entry_ids(led.cutoff_flags())
    daily = np.zeros(horizon + 10)
    for rec in led.records:
        if rec["kind"] == kind and rec["entry_id"] not in dropped:
            daily[min(max(rec["rec_day"], 0), horizon + 9)] += rec["amount"]
    outsider = np.cumsum(daily)[:horizon]
    insider = insider_path(econ, kind, horizon)
    truth = {e.entry_id: e for e in econ.entries}
    delays = [
        (truth[r["entry_id"]].true_amount, r["rec_day"] - truth[r["entry_id"]].true_day)
        for r in led.records
        if r["kind"] == kind and truth[r["entry_id"]].true_amount and econ.in_year(r["eff_day"])
    ]
    # misstatements that the outsiders' own treatment of the published records neutralises
    # (labels are used only to score the result, as in the audit regimes)
    neutral = set(dropped)
    neutral |= {i for i in late if truth[i].misstatement == "backdate"}
    neutral |= {i for i in econ.tampering if i in truth and truth[i].kind == kind}
    res = AuditResult("public", len(econ.year_entries(kind)), detected=neutral, corrected=set(neutral))
    shown = [
        r for r in led.records if r["kind"] == kind and econ.in_year(r["eff_day"]) and r["entry_id"] not in dropped
    ]
    revenue = sum(r["amount"] for r in shown)
    named = sum(r["amount"] for r in shown if r["counterparty"]) / revenue if revenue else 0.0
    start = econ.period_bounds[0][1] + report_lag
    return DisclosureResult(
        "public",
        np.arange(horizon),
        insider,
        outsider,
        start,
        _gap(insider, outsider, start),
        _weighted_delay(delays),
        0.0 if pseudonymize else named,
        1.0,
        evaluate(econ, res, kind),
        volume_linkability=named,
    )


def attested(
    econ: Economy,
    audit: CrossLedgerAudit | None = None,
    report_lag: int = 45,
    rng: np.random.Generator | int = 0,
    detection: DetectionModel | None = None,
    costs: CostModel | None = None,
    kind: str = "sale",
    attest_lag: int = 1,
) -> DisclosureResult:
    """Committed ledger with daily aggregates attested after cross-ledger matching.

    Each day's postings are matched once using only mirror records available at the publication day.
    A discovered error affects that day's aggregate; later evidence does not rewrite earlier releases.
    Residual monetary sampling is a year-end procedure and is not supported in this daily model."""
    econ.require_ground_truth()
    rng = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
    audit = audit or CrossLedgerAudit(residual_sampling=False)
    if audit.residual_sampling:
        raise ValueError("daily attestation requires residual_sampling=False")
    integer("attest_lag", attest_lag)
    integer("report_lag", report_lag)
    horizon = econ.fiscal_end + 1
    det, cm = detection or DetectionModel(), costs or CostModel()
    validate_mirror_owners(econ)
    published = build_ledger(econ, audit.close_lag, tampered=False)
    truth = {e.entry_id: e for e in econ.entries}
    pop = {e.entry_id: e for e in econ.year_entries(kind)}
    res = AuditResult("attested", len(pop))
    adopters = econ.adopters()
    backend = get_backend(audit.psi_backend)

    # Cut-off, forward-date and duplicate status of an entry depend only on
    # its own fields and earlier postings.  Database tampering is timed below.
    flag_ids: set[str] = set()
    if audit.ledger_checks:
        idx = set(published.cutoff_flags()) | set(published.forward_flags())
        idx |= set(published.duplicate_flags((kind,)))
        flag_ids = published.entry_ids(idx)
        res.add_cost("ledger", cm.ledger_setup + cm.per_entry_auto * len(published.records))

    # Mirror day is the model's proxy for evidence availability.  Preserve
    # the counterparty's original record order for duplicate occurrences.
    mirrors = sorted(
        (m.day, cp, j, m)
        for cp, records in econ.mirrors.items()
        for j, m in enumerate(records)
        if m.kind == kind and cp in adopters
    )
    available: dict[tuple[str, str], list[tuple[int, CounterRecord]]] = defaultdict(list)
    cursor = 0
    consumed: Counter = Counter()
    daily = np.zeros(horizon + 10)
    delays = []
    records = (r for r in published.records if r["kind"] == kind)
    for day, group in groupby(records, key=lambda r: r["rec_day"] + attest_lag):
        batch = list(group)
        while cursor < len(mirrors) and mirrors[cursor][0] <= day:
            _, cp, pos, mirror = mirrors[cursor]
            if has_document_id(mirror.doc_id):
                insort(available[(cp, mirror.doc_id)], (pos, mirror))
            cursor += 1
        candidate_records, left, right = [], [], []
        batch_occurrences: Counter = Counter()
        requested: set[tuple[str, str]] = set()
        unmatched: set[str] = set()
        for rec in batch:
            cp, eid = rec["counterparty"], rec["entry_id"]
            if cp not in adopters or eid not in pop:
                continue
            if not has_document_id(rec["doc_id"]):
                res.checked.add(eid)
                unmatched.add(eid)
                continue
            key = (cp, *matching_key(rec["doc_id"], rec["eff_day"], rec["amount"], kind))
            batch_occurrences[key] += 1
            occ = consumed[key] + batch_occurrences[key]
            # Domain separation prevents matching one counterparty's record to another's.
            domain = str(len(cp.encode("utf-8"))).encode() + b":" + cp.encode("utf-8")
            left.append(domain + fingerprint(rec["doc_id"], rec["eff_day"], rec["amount"], kind, occ))
            requested.add((cp, rec["doc_id"]))
            candidate_records.append(rec)
        for cp, doc_id in sorted(requested):
            domain = str(len(cp.encode("utf-8"))).encode() + b":" + cp.encode("utf-8")
            peer_occurrences: Counter = Counter()
            for _, m in available.get((cp, doc_id), []):
                if not has_document_id(m.doc_id):
                    continue
                peer_key = matching_key(m.doc_id, m.day, m.amount, kind)
                peer_occurrences[peer_key] += 1
                right.append(domain + fingerprint(m.doc_id, m.day, m.amount, kind, peer_occurrences[peer_key]))
        if candidate_records:
            membership, stats = intersect(left, right, backend)
            res.psi.append(stats)
            for rec, ok in zip(candidate_records, membership):
                eid = rec["entry_id"]
                res.checked.add(eid)
                if ok:
                    res.confirmed.add(eid)
                    key = (rec["counterparty"], *matching_key(rec["doc_id"], rec["eff_day"], rec["amount"], kind))
                    consumed[key] += 1
                else:
                    unmatched.add(eid)

        current_flags = (flag_ids | unmatched) & {r["entry_id"] for r in batch} & set(pop)
        if audit.ledger_checks:
            for rec in batch:
                eid = rec["entry_id"]
                if eid in pop and eid in econ.tampering:
                    e = pop[eid]
                    default_day = econ.period_bounds[min(econ.period_of(e.eff_day), len(econ.period_bounds) - 1)][1] + 6
                    if max(e.rec_day, econ.tamper_days.get(eid, default_day)) <= day:
                        current_flags.add(eid)
        to_investigate = []
        for eid in sorted(current_flags):
            e = pop[eid]
            event_day = max(
                e.rec_day,
                econ.tamper_days.get(
                    eid, econ.period_bounds[min(econ.period_of(e.eff_day), len(econ.period_bounds) - 1)][1] + 6
                ),
            )
            if e.misstatement == "tamper" and event_day > day:
                e = e.copy(misstatement=None)
            to_investigate.append(e)
        _investigate(to_investigate, det, rng, res)
        if audit.correct:
            res.corrected |= res.detected
        for rec in batch:
            e = truth[rec["entry_id"]]
            amount = e.true_amount if rec["entry_id"] in res.corrected else rec["amount"]
            daily[min(max(day, 0), horizon + 9)] += amount
            if e.true_amount and econ.in_year(rec["eff_day"]):
                delays.append((e.true_amount, day - e.true_day))

    # An alteration to the firm's later copy never affected the posted stream.
    neutral_tamper = {i for i in econ.tampering if i in truth and truth[i].kind == kind}
    res.detected |= neutral_tamper
    res.corrected |= neutral_tamper
    res.matched = len(res.confirmed)
    res.add_cost("investigation", cm.per_investigation * res.investigated)
    if res.psi:
        elements = sum(s.n_a + s.n_b for s in res.psi)
        res.add_cost("psi", cm.psi_setup + cm.per_psi_element * elements)
    outsider = np.cumsum(daily)[:horizon]
    insider = insider_path(econ, kind, horizon)
    start = econ.period_bounds[0][1] + report_lag
    return DisclosureResult(
        "attested",
        np.arange(horizon),
        insider,
        outsider,
        start,
        _gap(insider, outsider, start),
        _weighted_delay(delays),
        0.0,
        0.0,
        evaluate(econ, res, kind),
    )


def run_regime(name: str, econ: Economy, **kw) -> DisclosureResult:
    fns: dict[str, Callable[..., DisclosureResult]] = {"periodic": periodic, "public": public, "attested": attested}
    fn = fns.get(name)
    if fn is None:
        raise ValueError(f"unknown disclosure regime {name!r}; choose from {REGIME_NAMES}")
    return fn(econ, **kw)
