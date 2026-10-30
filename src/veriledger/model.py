"""Core accounting data model: double-entry journal entries and counterparty mirror records.

Days are integer offsets from the start date of the simulated (or imported) fiscal year. Fields
prefixed ``true_`` and the ``misstatement`` label are ground truth used only for evaluation; they
are never shown to a verifier and are excluded from hashed ledger records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from numbers import Integral
from typing import Any

from .validation import finite, integer

#: Illustrative chart of accounts (loosely follows common four-digit numbering).
ACCOUNTS: dict[str, str] = {
    "1400": "Inventory",
    "1500": "Accounts receivable",
    "1920": "Bank",
    "2400": "Accounts payable",
    "3000": "Sales revenue",
    "4000": "Cost of goods sold",
    "6300": "Operating expenses",
}

#: Entry kinds produced by the simulator. Only ``sale`` and ``purchase`` have counterparty mirrors.
KINDS = ("sale", "purchase", "cogs", "receipt", "payment", "expense")
#: Further kinds assigned to imported entries: credit notes and supplier credit notes, and anything else.
IMPORT_KINDS = ("sale_return", "purchase_return", "other")

TOL = 1e-6


@dataclass(frozen=True)
class Line:
    """One posting line of a journal entry."""

    account: str
    debit: float = 0.0
    credit: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.account, str) or not self.account.strip():
            raise ValueError("account must be a non-empty string")
        finite("debit", self.debit, 0.0)
        finite("credit", self.credit, 0.0)
        if self.debit < 0 or self.credit < 0:
            raise ValueError("debit and credit must be non-negative")
        if self.debit > 0 and self.credit > 0:
            raise ValueError("a line is either a debit or a credit")


@dataclass
class Entry:
    """A balanced double-entry journal entry.

    ``eff_day`` is the transaction (economic or invoice) date claimed by the firm and ``rec_day`` is
    the system entry date on which the entry was recorded. ``amount`` is the gross amount as recorded.
    """

    entry_id: str
    kind: str
    eff_day: int
    rec_day: int
    lines: list[Line]
    amount: float
    counterparty: str | None = None
    doc_id: str | None = None
    description: str = ""
    # ground truth (evaluation only); default to the recorded values (set in __post_init__)
    true_amount: float = None  # type: ignore[assignment]
    true_day: int = None  # type: ignore[assignment]
    misstatement: str | None = None

    def __post_init__(self) -> None:
        self._validate_public_fields()
        if self.lines and abs(self.amount - self.debits) > TOL:
            raise ValueError("amount must equal the total debit of the posting lines")
        if self.true_amount is None:
            self.true_amount = self.amount
        if self.true_day is None:
            self.true_day = self.eff_day

    def _validate_public_fields(self) -> None:
        """Recheck mutable public fields before serialization or commitment."""
        finite("amount", self.amount, 0.0)
        if not isinstance(self.entry_id, str) or not self.entry_id.strip():
            raise ValueError("entry_id must be a non-empty string")
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("kind must be a non-empty string")
        for name in ("counterparty", "doc_id"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None")
        if not isinstance(self.description, str):
            raise ValueError("description must be a string")
        if not isinstance(self.lines, (list, tuple)) or any(not isinstance(line, Line) for line in self.lines):
            raise ValueError("lines must contain Line objects")
        for name in ("eff_day", "rec_day"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer day offset")

    @property
    def debits(self) -> float:
        return sum(ln.debit for ln in self.lines)

    @property
    def credits(self) -> float:
        return sum(ln.credit for ln in self.lines)

    def is_balanced(self, tol: float = TOL) -> bool:
        """Debits equal credits (zero-amount entries, which real general ledgers contain, are allowed)."""
        return bool(self.lines) and abs(self.debits - self.credits) <= tol

    def validate_record(self) -> None:
        """Validate the cent-rounded representation that is actually committed."""
        self._validate_public_fields()
        if not self.is_balanced():
            raise ValueError(f"entry {self.entry_id} is not balanced")
        dr = sum(round(ln.debit, 2) for ln in self.lines)
        cr = sum(round(ln.credit, 2) for ln in self.lines)
        if abs(dr - cr) > TOL or abs(dr - round(self.amount, 2)) > TOL:
            raise ValueError(f"entry {self.entry_id} is inconsistent after rounding to cents")

    def record(self) -> dict[str, Any]:
        """Public fields that enter the ledger hash (no ground truth)."""
        self._validate_public_fields()
        return {
            "entry_id": self.entry_id,
            "kind": self.kind,
            "eff_day": int(self.eff_day),
            "rec_day": int(self.rec_day),
            "amount": round(float(self.amount), 2),
            "counterparty": self.counterparty,
            "doc_id": self.doc_id,
            "description": self.description,
            "lines": [[ln.account, round(ln.debit, 2), round(ln.credit, 2)] for ln in self.lines],
        }

    def canonical(self) -> bytes:
        """Canonical JSON encoding of :meth:`record` (sorted keys, no whitespace)."""
        return json.dumps(self.record(), sort_keys=True, separators=(",", ":")).encode("utf-8")

    def copy(self, **changes: Any) -> Entry:
        changes.setdefault("lines", list(self.lines))
        return replace(self, **changes)

    def validate_truth(self) -> None:
        """Check evaluation-only fields without adding them to public records."""
        from .misstate import SCHEMES

        finite("true_amount", self.true_amount, 0.0)
        if isinstance(self.true_day, bool) or not isinstance(self.true_day, Integral):
            raise ValueError("true_day must be an integer day offset")
        if self.misstatement is not None and (
            not isinstance(self.misstatement, str) or self.misstatement not in SCHEMES
        ):
            raise ValueError("unknown misstatement label")


@dataclass(frozen=True)
class CounterRecord:
    """The counterparty's own record of a transaction with the focal firm.

    For a sale by the focal firm, the customer holds a purchase record; for a purchase, the supplier
    holds a sale record. Dates and amounts are the true invoice values.
    """

    owner: str
    doc_id: str | None
    day: int
    amount: float
    kind: str  # kind of the focal firm's entry this record mirrors: "sale" or "purchase"

    def __post_init__(self) -> None:
        if not isinstance(self.owner, str) or not self.owner.strip():
            raise ValueError("mirror owner must be a non-empty string")
        if self.doc_id is not None and not isinstance(self.doc_id, str):
            raise ValueError("mirror doc_id must be a string or None")
        if isinstance(self.day, bool) or not isinstance(self.day, Integral):
            raise ValueError("mirror day must be an integer offset")
        finite("mirror amount", self.amount, 0.0)
        if self.kind not in ("sale", "purchase"):
            raise ValueError("mirror kind must be sale or purchase")


@dataclass
class Party:
    party_id: str
    role: str  # "customer" or "supplier"
    adopts: bool = False
    related: bool = False
    weight: float = 1.0


@dataclass
class Economy:
    """The focal firm's journal, its counterparties and their mirror records."""

    firm: str
    entries: list[Entry]
    parties: dict[str, Party]
    mirrors: dict[str, list[CounterRecord]] = field(default_factory=dict)
    period_bounds: list[tuple[int, int]] = field(default_factory=list)
    start_date: str = "2025-01-01"
    cost_ratio: float = 0.6
    #: after-the-fact alterations of recorded amounts: entry_id -> altered amount
    tampering: dict[str, float] = field(default_factory=dict)
    #: last simulated day; days after ``fiscal_end`` belong to the following fiscal year
    last_day: int | None = None
    tamper_days: dict[str, int] = field(default_factory=dict)
    #: False when the truth sidecar was deliberately omitted; observed records are not ground truth.
    has_ground_truth: bool = True

    def require_ground_truth(self) -> None:
        if not isinstance(self.has_ground_truth, bool):
            raise ValueError("has_ground_truth must be a boolean")
        if not self.has_ground_truth:
            raise ValueError("this simulation requires ground truth; the economy was loaded without truth.csv")
        for entry in self.entries:
            entry.validate_truth()

    @property
    def fiscal_end(self) -> int:
        """Last day of the fiscal year (the end of the last period)."""
        return self.period_bounds[-1][1]

    def in_year(self, day: int) -> bool:
        return self.period_bounds[0][0] <= day <= self.fiscal_end

    def period_of(self, day: int) -> int:
        """Index of the period containing ``day``; ``len(period_bounds)`` for days after the fiscal year."""
        for k, (a, b) in enumerate(self.period_bounds):
            if a <= day <= b:
                return k
        return len(self.period_bounds) if day > self.fiscal_end else 0

    def year_entries(self, kind: str | None = None, apparent: bool = False) -> list[Entry]:
        """Entries dated in the fiscal year (optionally of one kind, optionally after tampering)."""
        ents = self.apparent_entries() if apparent else self.entries
        return [e for e in ents if self.in_year(e.eff_day) and (kind is None or e.kind == kind)]

    def by_kind(self, kind: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == kind]

    def adopters(self) -> set[str]:
        return {p for p, v in self.parties.items() if v.adopts}

    def misstated(self) -> list[Entry]:
        return [e for e in self.entries if e.misstatement]

    def copy(self) -> Economy:
        return Economy(
            firm=self.firm,
            entries=[e.copy() for e in self.entries],
            parties={k: replace(v) for k, v in self.parties.items()},
            mirrors={k: list(v) for k, v in self.mirrors.items()},
            period_bounds=list(self.period_bounds),
            start_date=self.start_date,
            cost_ratio=self.cost_ratio,
            tampering=dict(self.tampering),
            last_day=self.last_day,
            tamper_days=dict(self.tamper_days),
            has_ground_truth=self.has_ground_truth,
        )

    def as_of(self, day: int) -> Economy:
        """Snapshot available on a decision day; mirror dates proxy their availability."""
        integer("decision day", day)
        out = self.copy()
        out.entries = [e for e in out.entries if e.rec_day <= day]
        ids = {e.entry_id for e in out.entries}
        timing = {}
        for e in self.entries:
            default = self.period_bounds[min(self.period_of(e.eff_day), len(self.period_bounds) - 1)][1] + 6
            timing[e.entry_id] = max(e.rec_day, self.tamper_days.get(e.entry_id, default))
        out.tampering = {k: v for k, v in out.tampering.items() if k in ids and timing[k] <= day}
        out.tamper_days = {k: timing[k] for k in out.tampering}
        for e in out.entries:
            if e.misstatement == "tamper" and e.entry_id not in out.tampering:
                e.misstatement = None
        out.mirrors = {k: [m for m in v if m.day <= day] for k, v in out.mirrors.items()}
        out.last_day = day
        return out

    def apparent_entries(self) -> list[Entry]:
        """Entries as they appear in a conventional (alterable) database, i.e. after tampering."""
        out = []
        for e in self.entries:
            if e.entry_id in self.tampering:
                out.append(with_amount(e, self.tampering[e.entry_id]))
            else:
                out.append(e)
        return out


def make_entry(
    entry_id: str,
    kind: str,
    day: int,
    amount: float,
    *,
    rec_day: int | None = None,
    counterparty: str | None = None,
    doc_id: str | None = None,
    description: str = "",
) -> Entry:
    """Create a balanced entry of a standard kind."""
    finite("amount", amount, 0.0)
    amount = round(float(amount), 2)
    dr, cr = {
        "sale": ("1500", "3000"),
        "purchase": ("1400", "2400"),
        "cogs": ("4000", "1400"),
        "receipt": ("1920", "1500"),
        "payment": ("2400", "1920"),
        "expense": ("6300", "1920"),
    }[kind]
    return Entry(
        entry_id=entry_id,
        kind=kind,
        eff_day=day,
        rec_day=day if rec_day is None else rec_day,
        lines=[Line(dr, debit=amount), Line(cr, credit=amount)],
        amount=amount,
        counterparty=counterparty,
        doc_id=doc_id,
        description=description or kind,
    )


def with_amount(entry: Entry, amount: float) -> Entry:
    """Return a copy of a two-line entry with both lines rescaled to ``amount`` (keeps ground truth)."""
    finite("amount", amount, 0.0)
    amount = round(float(amount), 2)
    if len(entry.lines) != 2 or not entry.is_balanced():
        raise ValueError("amount adjustment requires a balanced two-line entry")
    debit = [i for i, line in enumerate(entry.lines) if line.debit > 0]
    credit = [i for i, line in enumerate(entry.lines) if line.credit > 0]
    if not debit and not credit:
        # Zero-value entries contain no side information; infer only the
        # standard synthetic account pairs, not arbitrary imported entries.
        pairs = {
            "sale": ("1500", "3000"),
            "purchase": ("1400", "2400"),
            "cogs": ("4000", "1400"),
            "receipt": ("1920", "1500"),
            "payment": ("2400", "1920"),
            "expense": ("6300", "1920"),
        }
        pair = pairs.get(entry.kind)
        accounts = [ln.account for ln in entry.lines]
        if pair is None or pair[0] == pair[1] or sorted(accounts) != sorted(pair):
            raise ValueError("cannot infer posting direction for a zero-value entry")
        debit, credit = [accounts.index(pair[0])], [accounts.index(pair[1])]
    if len(debit) != 1 or len(credit) != 1 or debit[0] == credit[0]:
        raise ValueError("amount adjustment requires one debit line and one credit line")
    lines = [
        Line(ln.account, amount if i == debit[0] else 0.0, amount if i == credit[0] else 0.0)
        for i, ln in enumerate(entry.lines)
    ]
    return entry.copy(amount=amount, lines=lines)
