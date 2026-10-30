"""Inject labelled misstatements into a synthetic economy.

Six schemes are supported. Each changes the focal firm's records and, where the scheme implies it,
the counterparty's mirror records, so that verification procedures face realistic evidence.

* ``backdate``: a genuine sale made shortly after a period end (including the fiscal year end, when the
  following year is simulated) is dated inside the earlier period (cut-off manipulation), and so is
  the cost of sales posted with it. The entries are still recorded when the goods actually ship.
* ``fictitious``: a sale (or purchase) with no underlying transaction; the counterparty has no record.
  A fictitious sale also posts a cost of sales, as an invoice entered in the sales module would.
* ``inflate``: the recorded amount exceeds the invoice amount held by the counterparty.
* ``duplicate``: the same invoice is recorded twice.
* ``round_trip``: a sale to a related party (with its cost of sales) that is reversed by a purchase from
  it in the next period (for the last period, in the following year if it is simulated). The related
  party colludes and records matching entries.
* ``tamper``: the amount of a recorded entry is altered in the firm's database after the period closes.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, replace

import numpy as np

from .model import CounterRecord, Economy, make_entry, with_amount
from .validation import finite, integer

SCHEMES = ("backdate", "fictitious", "inflate", "duplicate", "round_trip", "tamper")


@dataclass
class MisstatementSpec:
    backdate: float = 0.0  # share of sales in the first ``backdate_window`` days of a period moved back
    backdate_window: int = 10
    fictitious: float = 0.0  # number of fictitious entries as a share of genuine target entries
    inflate: float = 0.0  # share of target entries whose amount is inflated
    inflate_factor: float = 0.25
    duplicate: float = 0.0  # share of target entries recorded twice
    round_trip: int = 0  # number of round-trip pairs with related parties
    round_trip_mean: float = 10000.0
    tamper: float = 0.0  # share of target entries altered after the period closes
    tamper_factor: float = 0.3
    target: str = "sale"  # kind targeted by fictitious/inflate/duplicate/tamper
    end_of_period_days: int = 15

    @classmethod
    def from_dict(cls, d: dict | None) -> MisstatementSpec:
        d = dict(d or {})
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown misstatement parameters: {sorted(unknown)}")
        spec = cls(**d)
        spec.validate()
        return spec

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if name != "target":
                finite(name, value, 0.0)
        integer("round_trip", self.round_trip)
        integer("backdate_window", self.backdate_window, 1)
        integer("end_of_period_days", self.end_of_period_days, 1)
        if self.target not in ("sale", "purchase"):
            raise ValueError("target must be 'sale' or 'purchase'")
        for name in ("backdate", "inflate", "duplicate", "tamper"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} is a share and must be in [0, 1]")
        if self.fictitious < 0 or self.round_trip < 0 or int(self.round_trip) != self.round_trip:
            raise ValueError("fictitious must be >= 0 and round_trip a non-negative integer")
        if self.backdate_window < 1 or self.end_of_period_days < 1:
            raise ValueError("backdate_window and end_of_period_days must be at least 1")
        if self.inflate_factor <= 0 or self.tamper_factor == 0 or self.round_trip_mean <= 0:
            raise ValueError("inflate_factor and round_trip_mean must be positive, tamper_factor positive")

    def to_dict(self) -> dict:
        return asdict(self)


def _pick(rng: np.random.Generator, items: list, share: float) -> list:
    k = int(round(share * len(items)))
    if k <= 0 or not items:
        return []
    idx = rng.choice(len(items), size=min(k, len(items)), replace=False)
    return [items[i] for i in sorted(idx)]


def _lag(rng: np.random.Generator) -> int:
    return int(rng.choice([0, 1, 2, 3], p=[0.6, 0.25, 0.1, 0.05]))


def inject(
    econ: Economy,
    spec: MisstatementSpec | dict | None,
    seed: int | np.random.Generator = 0,
    renumber: bool = True,
) -> Economy:
    """Return a copy of ``econ`` with the misstatements in ``spec`` injected and labelled.

    With ``renumber`` (the default) all entries are then numbered in recording order and all invoices in
    the order they were issued, so identifiers reveal nothing about which entries are misstated.
    """
    econ.require_ground_truth()
    spec = spec if isinstance(spec, MisstatementSpec) else MisstatementSpec.from_dict(spec)
    spec.validate()
    rng = seed if isinstance(seed, np.random.Generator) else np.random.default_rng(seed)
    out = econ.copy()
    bounds = out.period_bounds
    entries = out.entries
    counter = {"n": 0}
    reserved_ids = {e.entry_id for e in entries}
    reserved_docs = {e.doc_id for e in entries if e.doc_id}
    reserved_docs.update(m.doc_id for records in out.mirrors.values() for m in records if m.doc_id is not None)

    def new_id(prefix: str) -> str:  # temporary identifiers, replaced when renumbering
        while True:
            counter["n"] += 1
            candidate = f"~{prefix}{counter['n']:06d}"
            if candidate not in reserved_ids:
                reserved_ids.add(candidate)
                return candidate

    def new_doc(prefix: str) -> str:
        number = counter["n"] + 1
        while (candidate := f"{prefix}{number:06d}") in reserved_docs:
            number += 1
        reserved_docs.add(candidate)
        return candidate

    def genuine(kind: str, year_only: bool = True) -> list[int]:
        """Unmisstated entries of ``kind``, by default only those dated in the fiscal year."""
        return [
            i
            for i, e in enumerate(entries)
            if e.kind == kind and not e.misstatement and (not year_only or out.in_year(e.eff_day))
        ]

    if (spec.inflate > 0 or spec.tamper > 0) and any(len(entries[i].lines) != 2 for i in genuine(spec.target)):
        raise ValueError("inflate and tamper need two-line entries of the standard kinds (see make_entry)")

    def replace_at(i: int, e) -> None:
        entries[i] = e

    # the cost of sales that the accounting system posts with each sale, and the firm's own cost ratios
    cogs_of: dict[str, int] = {}
    for i, e in enumerate(entries):
        if e.kind == "cogs" and e.doc_id:
            cogs_of.setdefault(e.doc_id, i)
    sale_amount = {e.doc_id: e.amount for e in entries if e.kind == "sale" and e.doc_id and not e.misstatement}
    ratios = [entries[i].amount / sale_amount[d] for d, i in cogs_of.items() if sale_amount.get(d)]

    def cost_of(amount: float) -> float:
        return round(amount * (float(rng.choice(ratios)) if ratios else out.cost_ratio), 2)

    # backdating (always on sales): sales made just after a period end, including the fiscal year end
    # when the following year is simulated, are dated in the last days of the preceding period; the cost
    # of sales posted with the invoice moves with it
    if spec.backdate > 0:
        # (first day after a period end, first day of the period that ends there)
        boundaries = [(bounds[k][0], bounds[k - 1][0]) for k in range(1, len(bounds))]
        boundaries.append((out.fiscal_end + 1, bounds[-1][0]))
        boundary_of: dict[int, tuple[int, int]] = {}
        for i in genuine("sale", False):
            for a, prev in boundaries:  # increasing order: the nearest preceding boundary wins
                if a <= entries[i].eff_day < a + spec.backdate_window:
                    boundary_of[i] = (a, prev)
        for i in _pick(rng, sorted(boundary_of), spec.backdate):
            e = entries[i]
            a, prev = boundary_of[i]
            new_day = max(prev, a - 1 - int(rng.integers(0, 4)))
            replace_at(i, e.copy(eff_day=new_day, misstatement="backdate", true_day=e.eff_day))
            j = cogs_of.get(e.doc_id) if e.doc_id else None
            if j is not None and entries[j].eff_day == e.eff_day and not entries[j].misstatement:
                c = entries[j]
                replace_at(j, c.copy(eff_day=new_day, misstatement="backdate", true_day=c.eff_day))

    target = spec.target
    parties = out.parties
    role = "customer" if target == "sale" else "supplier"
    cps = [p for p, v in parties.items() if v.role == role and not v.related]
    if spec.fictitious > 0 and not cps:
        raise ValueError(f"fictitious entries need at least one {role} in econ.parties")
    w = np.array([parties[p].weight for p in cps], dtype=float)
    w = w / w.sum() if w.size else w
    mean_amt = float(np.mean([entries[i].amount for i in genuine(target)])) if genuine(target) else 1000.0

    # fictitious entries
    n_fict = int(round(spec.fictitious * len(genuine(target))))
    for _ in range(n_fict):
        k = int(rng.integers(0, len(bounds)))
        a, b = bounds[k]
        day = int(rng.integers(max(a, b - spec.end_of_period_days + 1), b + 1))
        cp = str(rng.choice(cps, p=w))
        amt = round(float(rng.lognormal(np.log(mean_amt) - 0.5 * 0.8**2, 0.8)), 2)
        doc = new_doc("INV-~F" if target == "sale" else "PI-~F")
        e = make_entry(
            new_id("F"),
            target,
            day,
            amt,
            rec_day=day + _lag(rng),
            counterparty=cp,
            doc_id=doc,
            description="Sale of goods" if target == "sale" else "Purchase of goods",
        )
        e.true_amount = 0.0
        e.misstatement = "fictitious"
        entries.append(e)
        if target == "sale":  # the fictitious invoice also relieves inventory, as the sales module would
            c = make_entry(
                new_id("F"), "cogs", day, cost_of(amt), rec_day=e.rec_day, doc_id=doc, description="Cost of goods sold"
            )
            c.true_amount, c.misstatement = 0.0, "fictitious"
            entries.append(c)

    # inflated amounts
    for i in _pick(rng, genuine(target), spec.inflate):
        e = entries[i]
        replace_at(
            i, with_amount(e, e.amount * (1 + spec.inflate_factor)).copy(misstatement="inflate", true_amount=e.amount)
        )

    # duplicates
    for i in _pick(rng, genuine(target), spec.duplicate):
        e = entries[i]
        dup = e.copy(
            entry_id=new_id("D"), rec_day=e.rec_day + int(rng.integers(1, 6)), misstatement="duplicate", true_amount=0.0
        )
        entries.append(dup)

    # round trips with related parties (sale now, purchase back in the next period)
    related = [p for p, v in parties.items() if v.related]
    if spec.round_trip > 0 and not related:
        raise ValueError("round trips need related parties (EconomyConfig.related_parties > 0)")
    for _ in range(int(spec.round_trip)):
        rp = str(rng.choice(related))
        k = int(rng.integers(0, len(bounds)))
        a, b = bounds[k]
        day = int(rng.integers(max(a, b - spec.end_of_period_days + 1), b + 1))
        amt = round(float(rng.lognormal(np.log(spec.round_trip_mean) - 0.5 * 0.3**2, 0.3)), 2)
        doc = new_doc("INV-~R")
        sale = make_entry(
            new_id("R"),
            "sale",
            day,
            amt,
            rec_day=day + _lag(rng),
            counterparty=rp,
            doc_id=doc,
            description="Sale of goods",
        )
        sale.true_amount, sale.misstatement = 0.0, "round_trip"
        entries.append(sale)
        c = make_entry(
            new_id("R"), "cogs", day, cost_of(amt), rec_day=sale.rec_day, doc_id=doc, description="Cost of goods sold"
        )
        c.true_amount, c.misstatement = 0.0, "round_trip"
        entries.append(c)
        out.mirrors.setdefault(rp, []).append(CounterRecord(rp, doc, day, amt, "sale"))
        last = out.last_day if out.last_day is not None else out.fiscal_end
        back = min(b + 1 + int(rng.integers(5, 30)), max(last, b))
        pdoc = new_doc("PI-~R")
        buy = make_entry(
            new_id("R"),
            "purchase",
            back,
            amt,
            rec_day=back + _lag(rng),
            counterparty=rp,
            doc_id=pdoc,
            description="Purchase of goods",
        )
        buy.true_amount, buy.misstatement = 0.0, "round_trip"
        entries.append(buy)
        out.mirrors[rp].append(CounterRecord(rp, pdoc, back, amt, "purchase"))

    # tampering after the period closes (recorded amount unchanged, database copy altered)
    for i in _pick(rng, genuine(target), spec.tamper):
        e = entries[i]
        replace_at(i, e.copy(misstatement="tamper"))
        out.tampering[e.entry_id] = round(e.amount * (1 + spec.tamper_factor), 2)
        out.tamper_days[e.entry_id] = max(e.rec_day, bounds[out.period_of(e.eff_day)][1] + 6)

    if renumber and (len(out.misstated()) != len(econ.misstated()) or len(out.entries) != len(econ.entries)):
        renumber_economy(out, rng)
    return out


_DOC_REF = re.compile(r"(INV|PI)-[~\w]+")


def renumber_economy(econ: Economy, rng: np.random.Generator | None = None) -> None:
    """Number entries ``E0000001, ...`` in recording order and invoices ``INV-``/``PI-`` in issue order.

    A backdated invoice keeps the place in the sequence of the day it was actually issued, as in a real
    numbering system. Ties are broken at random (``rng``) or by the current order. The entries, counterparty
    records, payment descriptions and the tampering map of ``econ`` are updated in place.
    """
    entries = econ.entries

    def ties(n: int) -> np.ndarray:
        return rng.random(n) if rng is not None else np.arange(n, dtype=float)

    issue: dict[str, int] = {}
    series: dict[str, str] = {}
    for e in entries:
        if e.kind in ("sale", "purchase") and e.doc_id:
            day = int(e.true_day if e.misstatement == "backdate" else e.eff_day)
            issue[e.doc_id] = min(issue.get(e.doc_id, day), day)
            series[e.doc_id] = "INV" if e.kind == "sale" else "PI"
    docmap: dict[str, str] = {}
    # References absent from the firm's invoices remain unchanged; do not allocate their names.
    unmapped_docs = {e.doc_id for e in entries if e.doc_id} | {
        m.doc_id for records in econ.mirrors.values() for m in records
    }
    unmapped_docs.difference_update(issue)
    for prefix in ("INV", "PI"):
        docs = [d for d in issue if series[d] == prefix]
        t = ties(len(docs))
        number = 1
        for j in sorted(range(len(docs)), key=lambda j: (issue[docs[j]], t[j])):
            while f"{prefix}-{number:06d}" in unmapped_docs:
                number += 1
            docmap[docs[j]] = f"{prefix}-{number:06d}"
            number += 1
    t = ties(len(entries))
    order = sorted(range(len(entries)), key=lambda j: (entries[j].rec_day, t[j]))
    idmap = {entries[j].entry_id: f"E{n:07d}" for n, j in enumerate(order, 1)}

    def fix_text(text: str) -> str:
        return _DOC_REF.sub(lambda m: docmap.get(m.group(0), m.group(0)), text)

    for e in entries:  # in place: the entries belong to this economy (inject works on a copy)
        e.entry_id = idmap[e.entry_id]
        if e.doc_id:
            e.doc_id = docmap.get(e.doc_id, e.doc_id)
        if "INV-" in e.description or "PI-" in e.description:
            e.description = fix_text(e.description)
    econ.entries = [entries[j] for j in order]
    econ.mirrors = {
        p: [m if m.doc_id is None or m.doc_id not in docmap else replace(m, doc_id=docmap[m.doc_id]) for m in recs]
        for p, recs in econ.mirrors.items()
    }
    econ.tampering = {idmap[k]: v for k, v in econ.tampering.items()}
    econ.tamper_days = {idmap[k]: v for k, v in econ.tamper_days.items()}


def overstatement(entry, tampered_amount: float | None = None) -> float:
    """Effect of a misstated entry on the reported amount of its claimed period."""
    if entry.misstatement == "backdate":
        return float(entry.amount)
    if entry.misstatement == "tamper":
        return float((tampered_amount if tampered_amount is not None else entry.amount) - entry.true_amount)
    return float(entry.amount - entry.true_amount)


def annual_overstatement(entry, fiscal_end: int, tampered_amount: float | None = None) -> float:
    """Effect of a misstated entry on the reported total of the fiscal year.

    Backdating between periods of the same year shifts revenue but leaves the annual total unchanged;
    backdating across the year end overstates it by the full amount."""
    if entry.misstatement == "backdate":
        return float(entry.amount) if entry.true_day > fiscal_end else 0.0
    return overstatement(entry, tampered_amount)
