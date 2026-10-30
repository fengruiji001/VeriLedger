"""Inventory-backed lending with and without verifiable purchase records.

A lender advances credit against the firm's reported inventory at each period end. Verified collateral
receives the advance rate ``advance_verified``; unverified collateral receives ``advance_unverified``.
With perfect information the lender would advance ``advance_verified`` times true inventory, so

* ``over_advance`` = credit in excess of that benchmark (lender exposure to padded inventory), and
* ``shortfall`` = credit below it (financing withheld from genuine collateral).

Policies: ``trust`` (reported inventory at the verified rate), ``haircut`` (reported inventory at the
unverified rate), ``field_exam`` (sampling of purchase records each period, then the verified rate),
and ``ledger`` (cross-ledger matching with adopting suppliers; matched purchases count as verified).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audit import AuditResult, CostModel, CrossLedgerAudit, DetectionModel, SamplingAudit
from .metrics import evaluate
from .misstate import overstatement
from .model import Economy
from .validation import finite, integer

POLICIES = ("trust", "haircut", "field_exam", "ledger")


@dataclass
class LendingResult:
    policy: str
    credit: float
    over_advance: float
    shortfall: float
    verified_share: float
    cost_hours: float
    audit: dict

    def summary(self) -> dict:
        return {
            "policy": self.policy,
            "credit": self.credit,
            "over_advance": self.over_advance,
            "shortfall": self.shortfall,
            "verified_share": self.verified_share,
            "cost_hours": self.cost_hours,
            "recall": self.audit.get("recall", float("nan")),
        }


def _merge_procedures(total: AuditResult, current: AuditResult) -> None:
    """Retain discoveries/counts, but let current checks supersede stale evidence states."""
    for name in ("detected", "corrected", "checked", "flagged"):
        getattr(total, name).update(getattr(current, name))
    total.confirmed = (total.confirmed - current.checked) | current.confirmed
    total.cleared = (total.cleared - current.unresolved - current.detected) | current.cleared
    total.sampled += current.sampled
    total.investigated += current.investigated
    total.false_positives += current.false_positives
    total.unresolved = (total.unresolved | current.unresolved) - total.detected - total.cleared


def lend(
    econ: Economy,
    policy: str = "ledger",
    advance_verified: float = 0.8,
    advance_unverified: float = 0.5,
    opening_days: int = 30,
    exam: SamplingAudit | None = None,
    crossledger: CrossLedgerAudit | None = None,
    rng: np.random.Generator | int = 0,
    detection: DetectionModel | None = None,
    costs: CostModel | None = None,
) -> LendingResult:
    """Average credit outcomes over the period ends of one fiscal year."""
    econ.require_ground_truth()
    if policy not in POLICIES:
        raise ValueError(f"unknown lending policy {policy!r}; choose from {POLICIES}")
    finite("advance_verified", advance_verified, 0.0, 1.0)
    finite("advance_unverified", advance_unverified, 0.0, advance_verified)
    integer("opening_days", opening_days)
    rng = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
    cm = costs or CostModel()
    all_cogs = [e for e in econ.entries if e.kind == "cogs"]
    all_purchases = [e for e in econ.entries if e.kind == "purchase"]
    horizon = econ.fiscal_end + 1
    # An ex ante stock calibration shared by every policy, not evidence used for matching.
    opening = opening_days * sum(e.true_amount for e in econ.year_entries("cogs")) / horizon
    res = AuditResult(policy, len(econ.year_entries("purchase")))
    examined: set[str] = set()
    setup_paid: set[str] = set()
    credits, overs, shorts, shares = [], [], [], []
    for _, b in econ.period_bounds:
        snap = econ.as_of(b)
        purchases = [e for e in snap.year_entries("purchase", apparent=True) if e.eff_day <= b]
        cogs = [e for e in snap.year_entries("cogs", apparent=True) if e.eff_day <= b]
        available = {e.entry_id for e in purchases}
        # Every decision is made from its own snapshot. Later discoveries never rewrite earlier credit.
        verified: set[str] = set()
        if policy == "field_exam":
            examiner = exam or SamplingAudit(cutoff_per_boundary=0)
            ids = available - examined
            materiality = examiner.tolerable * sum(e.amount for e in purchases)
            r = examiner.run(
                snap, "purchase", rng, detection, cm, subset=ids, tolerable_amount=materiality, regime="field_exam"
            )
            examined |= ids
            _merge_procedures(res, r)
            res.add_cost("field exam", r.cost)
            # Stylised field-exam policy certifies the available purchase pool after the examination.
            verified = available - res.unresolved - (res.detected - res.corrected)
        elif policy == "ledger":
            checker = crossledger or CrossLedgerAudit(residual_sampling=False)
            r = checker.run(snap, "purchase", rng, detection, cm, regime="ledger")
            _merge_procedures(res, r)
            verified = (r.confirmed | r.cleared | res.corrected) - res.unresolved - (res.detected - res.corrected)
            for key, value in r.cost_breakdown.items():
                # Set-up is paid once; cumulative populations are rechecked at every decision.
                setup = cm.ledger_setup if key == "ledger" else (cm.psi_setup if key == "psi" and r.psi else 0.0)
                if setup and key in setup_paid:
                    value -= setup
                if setup:
                    setup_paid.add(key)
                res.add_cost(key, value)
        rep = opening + sum(e.amount for e in purchases) - sum(e.amount for e in cogs)
        true = (
            opening
            + sum(e.true_amount for e in all_purchases if 0 <= e.true_day <= b)
            - sum(e.true_amount for e in all_cogs if 0 <= e.true_day <= b)
        )
        corr = sum(overstatement(e) for e in purchases if e.entry_id in res.corrected)
        collateral = max(rep - corr, 0.0)
        if policy == "trust":
            v = 1.0
        elif policy == "haircut":
            v = 0.0
        else:
            # A corrected inflation retains its substantiated true component; a fictitious item retains zero.
            adjusted = {
                e.entry_id: max(e.true_amount if e.entry_id in res.corrected else e.amount, 0.0) for e in purchases
            }
            gross = sum(adjusted.values())
            ver = sum(amount for eid, amount in adjusted.items() if eid in verified)
            v = ver / gross if gross else 0.0
        credit = collateral * (v * advance_verified + (1 - v) * advance_unverified)
        bench = advance_verified * max(true, 0.0)
        credits.append(credit)
        overs.append(max(credit - bench, 0.0))
        shorts.append(max(bench - credit, 0.0))
        shares.append(v)
    res.matched = len(res.confirmed)
    ev = evaluate(econ, res, "purchase")
    return LendingResult(
        policy,
        float(np.mean(credits)),
        float(np.mean(overs)),
        float(np.mean(shorts)),
        float(np.mean(shares)),
        float(res.cost),
        ev,
    )
