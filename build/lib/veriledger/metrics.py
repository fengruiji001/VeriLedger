"""Evaluation against ground truth: detection, residual misstatement and cost."""

from __future__ import annotations

from collections import defaultdict

from .audit import AuditResult
from .misstate import SCHEMES, annual_overstatement, overstatement
from .model import Economy


def evaluate(econ: Economy, res: AuditResult, kind: str = "sale") -> dict:
    """Summarise an audit result: recall by count and by amount, residual overstatement and cost.

    ``*_annual`` measures count effects on the fiscal-year total only: backdating between periods of
    the same year shifts revenue without changing the total and so counts in the period measures but
    not in the annual ones."""
    econ.require_ground_truth()
    mis = [e for e in econ.year_entries(kind) if e.misstatement]
    out: dict = {
        "regime": res.regime,
        "population": res.population,
        "n_misstated": len(mis),
        "n_detected": 0,
        "amount_misstated": 0.0,
        "amount_detected": 0.0,
        "residual_overstatement": 0.0,
        "amount_misstated_annual": 0.0,
        "amount_detected_annual": 0.0,
        "amount_corrected": 0.0,
        "amount_corrected_annual": 0.0,
        "n_corrected": len(res.corrected),
        "checked": len(res.checked),
        "unmatched": len(res.checked - res.confirmed),
        "unresolved": len(res.unresolved),
        "residual_overstatement_annual": 0.0,
        "false_positives": res.false_positives,
        "investigated": res.investigated,
        "sampled": res.sampled,
        "matched": res.matched,
        "cost_hours": round(res.cost, 4),
    }
    per: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    for e in mis:
        tampered = econ.tampering.get(e.entry_id)
        eff = abs(overstatement(e, tampered))
        ann = abs(annual_overstatement(e, econ.fiscal_end, tampered))
        hit = e.entry_id in res.detected
        scheme = str(e.misstatement)
        per[scheme][0] += 1
        per[scheme][1] += int(hit)
        out["amount_misstated"] += eff
        out["amount_misstated_annual"] += ann
        if hit:
            out["n_detected"] += 1
            out["amount_detected"] += eff
            out["amount_detected_annual"] += ann
        if e.entry_id in res.corrected:
            out["amount_corrected"] += eff
            out["amount_corrected_annual"] += ann
        else:
            out["residual_overstatement"] += eff
            out["residual_overstatement_annual"] += ann
    nan = float("nan")
    out["recall"] = out["n_detected"] / out["n_misstated"] if mis else nan
    out["recall_amount"] = out["amount_detected"] / out["amount_misstated"] if out["amount_misstated"] else nan
    ma = out["amount_misstated_annual"]
    out["recall_amount_annual"] = out["amount_detected_annual"] / ma if ma else nan
    for s in SCHEMES:
        n, d = per.get(s, (0, 0))
        out[f"n_{s}"] = n
        out[f"recall_{s}"] = d / n if n else float("nan")
    # audit opinion from the monetary-unit sample, where the regime draws one
    tm = res.tolerable_amount
    out["tolerable_amount"] = tm
    out["upper_limit"] = res.upper_limit
    out["projected_misstatement"] = res.projected_misstatement
    out["opinion"] = res.opinion
    if res.opinion == "none":
        out["clean_opinion"] = out["audit_failure"] = out["false_modification"] = nan
    else:
        clean = res.opinion == "clean"
        out["clean_opinion"] = float(clean)
        # type II: a clean opinion although the misstatement left in the annual figures (after the
        # corrections of what was found) exceeds tolerable misstatement
        out["audit_failure"] = float(clean and out["residual_overstatement_annual"] > tm)
        # type I: a modified opinion although the books were not materially misstated to begin with
        out["false_modification"] = float(not clean and out["amount_misstated_annual"] <= tm)
    for k, v in res.cost_breakdown.items():
        out[f"cost_{k.replace(' ', '_')}"] = round(v, 4)
    return out
