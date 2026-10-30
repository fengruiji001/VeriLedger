"""Synthetic economy: one focal firm, its customers and suppliers, and their mirror records.

Besides the recording rules of an accounting system, the generator adds the irregularities of real
ledgers that keep simple structural tests from separating misstated from genuine entries: cost of sales
varies with the product mix, some invoices are paid late, in part or not at all, and related parties
also trade genuinely with the firm."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, fields

import numpy as np

from .formats import fiscal_bounds, to_day
from .misstate import renumber_economy
from .model import CounterRecord, Economy, Party, make_entry
from .validation import finite, integer
from .validation import periods as validate_periods


@dataclass
class EconomyConfig:
    """Parameters of the synthetic economy. Amounts are in currency units; time is in days."""

    firm: str = "FIRM"
    start_date: str = "2025-01-01"
    #: length of the fiscal year in days; ``None`` (the default) is the calendar year from ``start_date``,
    #: with periods that end at month ends when ``periods`` divides 12 (quarters by default)
    days: int | None = None
    periods: int = 4
    post_days: int = 30  # days of the following year also simulated, for cut-off at the year end
    n_customers: int = 40
    n_suppliers: int = 20
    sales_per_day: float = 6.0
    purchases_per_day: float = 2.5
    expenses_per_day: float = 1.0
    sale_mean: float = 2500.0
    sale_sigma: float = 0.9
    purchase_mean: float = 4000.0
    purchase_sigma: float = 0.7
    expense_mean: float = 800.0
    cost_ratio: float = 0.6  # mean cost of sales as a share of the sale amount
    cost_sigma: float = 0.2  # dispersion of the cost ratio across sales (log scale; product mix)
    customer_zipf: float = 1.1
    supplier_zipf: float = 1.0
    seasonality: float = 0.2
    record_lag_probs: tuple[float, ...] = (0.6, 0.25, 0.1, 0.05)
    payment_terms: int = 30
    late_payment: float = 0.05  # share of invoices paid 30 to 120 days after the due date
    unpaid: float = 0.01  # share of invoices not paid within the simulated horizon
    short_payment: float = 0.03  # share of customer invoices paid only in part (90-99%; disputed balance)
    adoption: float = 0.5
    adoption_by_size: bool = False
    related_parties: int = 2
    related_purchases: float = 0.02  # share of purchases bought from related parties (genuine trade)
    mirror_noise: float = 0.005

    @classmethod
    def from_dict(cls, d: dict | None) -> EconomyConfig:
        d = dict(d or {})
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown economy parameters: {sorted(unknown)}")
        if "record_lag_probs" in d:
            d["record_lag_probs"] = tuple(d["record_lag_probs"])
        return cls(**d)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["record_lag_probs"] = list(self.record_lag_probs)
        return d

    def validate(self) -> None:
        for name in ("periods", "n_customers", "n_suppliers"):
            integer(name, getattr(self, name), 1)
        for name in ("post_days", "related_parties", "payment_terms"):
            integer(name, getattr(self, name))
        if self.days is not None:
            integer("days", self.days, 1)
        for name, value in asdict(self).items():
            if name not in ("firm", "start_date", "days", "record_lag_probs", "adoption_by_size"):
                finite(name, value, 0.0)
        if not isinstance(self.adoption_by_size, bool):
            raise ValueError("adoption_by_size must be a boolean")
        if not self.record_lag_probs:
            raise ValueError("record_lag_probs must not be empty")
        for value in self.record_lag_probs:
            finite("record_lag_probs", value, 0.0, 1.0)
        validate_periods(fiscal_year_bounds(self))
        if self.periods < 1 or (self.days is not None and self.days < self.periods):
            raise ValueError("need at least one day per period")
        fiscal_year_bounds(self)  # checks the start date
        if self.post_days < 0:
            raise ValueError("post_days must not be negative")
        if self.n_customers < 1 or self.n_suppliers < 1:
            raise ValueError("need at least one customer and one supplier")
        for name in ("sales_per_day", "purchases_per_day", "expenses_per_day", "sale_sigma", "purchase_sigma"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        for name in ("sale_mean", "purchase_mean", "expense_mean"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("adoption", "cost_ratio", "mirror_noise", "late_payment", "unpaid", "short_payment"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if not 0.0 <= self.related_purchases <= 1.0:
            raise ValueError("related_purchases must be in [0, 1]")
        if self.late_payment + self.unpaid > 1.0:
            raise ValueError("late_payment + unpaid must not exceed 1")
        if self.cost_sigma < 0:
            raise ValueError("cost_sigma must not be negative")
        if not 0.0 <= self.seasonality < 1.0:
            raise ValueError("seasonality must be in [0, 1)")
        if self.payment_terms < 5:
            raise ValueError("payment_terms must be at least 5 days")
        if min(self.record_lag_probs) < 0 or abs(sum(self.record_lag_probs) - 1.0) > 1e-9:
            raise ValueError("record_lag_probs must be non-negative and sum to one")
        if not 0 <= self.related_parties < self.n_customers:
            raise ValueError("related_parties must be in [0, n_customers)")


def period_bounds(days: int, periods: int) -> list[tuple[int, int]]:
    """``periods`` periods of (nearly) equal length covering days ``0 .. days - 1``."""
    integer("days", days, 1)
    integer("periods", periods, 1)
    if periods > days:
        raise ValueError("need at least one day per period")
    edges = np.linspace(0, days, periods + 1).round().astype(int)
    return [(int(edges[k]), int(edges[k + 1]) - 1) for k in range(periods)]


def fiscal_year_bounds(cfg: EconomyConfig) -> list[tuple[int, int]]:
    """Period bounds of the simulated fiscal year: calendar months from ``start_date`` (``days=None``)
    or equal periods of an explicit length ``days``."""
    start = dt.date.fromisoformat(cfg.start_date)  # raises on an invalid date
    if cfg.days is not None:
        return period_bounds(cfg.days, cfg.periods)
    try:
        next_year = start.replace(year=start.year + 1)
    except ValueError:  # 29 February
        next_year = start.replace(year=start.year + 1, day=28)
    length = to_day(cfg.start_date, next_year.isoformat())
    if 12 % cfg.periods == 0:
        return fiscal_bounds(cfg.start_date, cfg.periods, 12)
    return period_bounds(length, cfg.periods)


def _zipf_weights(n: int, s: float) -> np.ndarray:
    w = np.arange(1, n + 1, dtype=float) ** -s
    return w / w.sum()


def _lognormal(rng: np.random.Generator, mean: float, sigma: float, size=None):
    return rng.lognormal(np.log(mean) - sigma**2 / 2, sigma, size)


def generate(cfg: EconomyConfig | None = None, seed: int | np.random.Generator = 0) -> Economy:
    """Generate one fiscal year of transactions for the focal firm and its counterparties, followed by
    ``post_days`` days of the next year (so that misstatements can cross the year end)."""
    cfg = cfg or EconomyConfig()
    cfg.validate()
    # separate streams: counterparty adoption does not change the transaction draws (common random numbers)
    if isinstance(seed, np.random.Generator):
        seeds = [int(s) for s in seed.integers(0, 2**63 - 1, size=2)]
    else:
        seeds = [int(s.generate_state(1)[0]) for s in np.random.SeedSequence(seed).spawn(2)]
    rng_adopt, rng = np.random.default_rng(seeds[0]), np.random.default_rng(seeds[1])
    bounds = fiscal_year_bounds(cfg)
    year = bounds[-1][1] + 1

    cw = _zipf_weights(cfg.n_customers, cfg.customer_zipf)
    sw = _zipf_weights(cfg.n_suppliers, cfg.supplier_zipf)
    parties: dict[str, Party] = {}
    for i in range(cfg.n_customers):
        pid = f"C{i + 1:03d}"
        parties[pid] = Party(pid, "customer", weight=float(cw[i]))
    for i in range(cfg.n_suppliers):
        pid = f"S{i + 1:03d}"
        parties[pid] = Party(pid, "supplier", weight=float(sw[i]))
    # related parties: the smallest customers, controlled by the focal firm
    for i in range(cfg.n_customers - cfg.related_parties, cfg.n_customers):
        parties[f"C{i + 1:03d}"].related = True
    # adoption of the shared ledger by counterparties
    for role in ("customer", "supplier"):
        # the adoption share applies to independent counterparties; related parties always adopt (below)
        ids = [p for p in parties if parties[p].role == role and not parties[p].related]
        n_adopt = int(round(cfg.adoption * len(ids)))
        if cfg.adoption_by_size:
            chosen = ids[:n_adopt]  # ids are ordered from largest to smallest
        else:
            order = list(rng_adopt.permutation(ids))  # nested: a higher share adds adopters to a lower one
            chosen = order[:n_adopt]
        for pid in chosen:
            parties[str(pid)].adopts = True
    for party in parties.values():  # related parties always run the shared ledger (they collude)
        if party.related:
            party.adopts = True

    # related parties also trade genuinely with the firm: they buy (with their Zipf weight) and sell to it
    cust_ids = [p for p in parties if parties[p].role == "customer"]
    cust_w = np.array([parties[p].weight for p in cust_ids])
    cust_w = cust_w / cust_w.sum()
    supp_ids = [p for p in parties if parties[p].role == "supplier"]
    related_ids = [p for p in parties if parties[p].related]

    lag_vals = np.arange(len(cfg.record_lag_probs))
    lag_p = np.asarray(cfg.record_lag_probs)
    entries = []
    mirrors: dict[str, list[CounterRecord]] = {p: [] for p in parties}
    n = {"e": 0, "inv": 0, "po": 0, "rc": 0, "py": 0, "ex": 0}

    def eid() -> str:
        n["e"] += 1
        return f"E{n['e']:07d}"

    def lag() -> int:
        return int(rng.choice(lag_vals, p=lag_p))

    def mirror_day(d: int) -> int:
        return d + 1 if rng.random() < cfg.mirror_noise else d

    def pay_day(d: int) -> int | None:
        """Day an invoice of day ``d`` is paid: on terms, late, or not within the horizon (``None``)."""
        u = rng.random()
        if u < cfg.unpaid:
            return None
        day = d + cfg.payment_terms + int(rng.integers(-5, 6))
        if u < cfg.unpaid + cfg.late_payment:
            day += int(rng.integers(30, 121))
        return day

    total = year + cfg.post_days
    for d in range(total):
        season = 1.0 + cfg.seasonality * np.sin(2 * np.pi * d / year)
        for _ in range(rng.poisson(cfg.sales_per_day * season)):
            c = str(rng.choice(cust_ids, p=cust_w))
            amt = round(float(_lognormal(rng, cfg.sale_mean, cfg.sale_sigma)), 2)
            n["inv"] += 1
            doc = f"INV-{n['inv']:06d}"
            r = d + lag()
            entries.append(
                make_entry(eid(), "sale", d, amt, rec_day=r, counterparty=c, doc_id=doc, description="Sale of goods")
            )
            mirrors[c].append(CounterRecord(c, doc, mirror_day(d), amt, "sale"))
            ratio = float(_lognormal(rng, cfg.cost_ratio, cfg.cost_sigma)) if cfg.cost_sigma else cfg.cost_ratio
            entries.append(
                make_entry(
                    eid(),
                    "cogs",
                    d,
                    round(amt * ratio, 2),
                    rec_day=r,
                    doc_id=doc,
                    description="Cost of goods sold",
                )
            )
            pay = pay_day(d)
            # part of the invoice may be disputed and stay open (drawn always, so that the fiscal year's
            # transactions do not depend on how many days of the next year are simulated)
            short, part = rng.random() < cfg.short_payment, float(rng.uniform(0.90, 0.99))
            if pay is not None and pay < total:
                paid = round(amt * part, 2) if short else amt
                n["rc"] += 1
                entries.append(
                    make_entry(
                        eid(),
                        "receipt",
                        pay,
                        paid,
                        rec_day=pay,
                        counterparty=c,
                        doc_id=f"RC-{n['rc']:06d}",
                        description=f"Payment for {doc}",
                    )
                )
        for _ in range(rng.poisson(cfg.purchases_per_day * season)):
            if related_ids and rng.random() < cfg.related_purchases:
                s = str(rng.choice(related_ids))
            else:
                s = str(rng.choice(supp_ids, p=sw))
            amt = round(float(_lognormal(rng, cfg.purchase_mean, cfg.purchase_sigma)), 2)
            n["po"] += 1
            doc = f"PI-{n['po']:06d}"
            r = d + lag()
            entries.append(
                make_entry(
                    eid(), "purchase", d, amt, rec_day=r, counterparty=s, doc_id=doc, description="Purchase of goods"
                )
            )
            mirrors[s].append(CounterRecord(s, doc, mirror_day(d), amt, "purchase"))
            pay = pay_day(d)
            if pay is not None and pay < total:
                n["py"] += 1
                entries.append(
                    make_entry(
                        eid(),
                        "payment",
                        pay,
                        amt,
                        rec_day=pay,
                        counterparty=s,
                        doc_id=f"PY-{n['py']:06d}",
                        description=f"Payment of {doc}",
                    )
                )
        for _ in range(rng.poisson(cfg.expenses_per_day)):
            amt = round(float(_lognormal(rng, cfg.expense_mean, 0.5)), 2)
            n["ex"] += 1
            entries.append(
                make_entry(
                    eid(),
                    "expense",
                    d,
                    amt,
                    rec_day=d + lag(),
                    doc_id=f"EX-{n['ex']:06d}",
                    description="Operating expense",
                )
            )
    econ = Economy(cfg.firm, entries, parties, mirrors, bounds, cfg.start_date, cfg.cost_ratio, last_day=total - 1)
    renumber_economy(econ)  # entry numbers follow recording order, as in an accounting system
    return econ
