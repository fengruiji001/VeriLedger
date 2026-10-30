"""Import and export: generic general-ledger CSV, SAF-T Financial XML and economy bundles.

The SAF-T reader extracts the ``GeneralLedgerEntries`` section of Norwegian SAF-T Financial files
(versions 1.10 to 1.40, namespace ``urn:StandardAuditFile-Taxation-Financial:NO``). Each
``Transaction`` becomes one entry: ``TransactionDate`` is the effective date and ``SystemEntryDate``
the recording date, which is what the ledger's cut-off check needs. The writer produces a minimal
file (header and general-ledger entries) for testing; it is not a complete SAF-T export.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from numbers import Integral
from pathlib import Path
from tempfile import TemporaryDirectory

from . import __version__
from .misstate import SCHEMES
from .model import CounterRecord, Economy, Entry, Line, Party, with_amount
from .validation import finite, integer, periods

SAFT_NS = "urn:StandardAuditFile-Taxation-Financial:NO"

GL_COLUMNS = [
    "entry_id",
    "date",
    "record_date",
    "account",
    "debit",
    "credit",
    "counterparty",
    "doc_id",
    "kind",
    "description",
]


def to_date(start: str, day: int) -> str:
    if isinstance(day, bool) or not isinstance(day, Integral):
        raise ValueError("day must be an integer offset")
    try:
        return (dt.date.fromisoformat(start) + dt.timedelta(days=int(day))).isoformat()
    except OverflowError as exc:
        raise ValueError("day offset is outside the supported date range") from exc


def to_day(start: str, date: str) -> int:
    return (dt.date.fromisoformat(date[:10]) - dt.date.fromisoformat(start)).days


# ---------------------------------------------------------------------- generic CSV
def _csv_rows(path: str | Path, required: set[str], mapping: dict[str, str] | None = None) -> list[dict]:
    """Read an unambiguous rectangular CSV, including header-only empty datasets."""
    mapping = mapping or {}
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        source = list(reader.fieldnames or [])
        columns = [mapping.get(name, name) for name in source]
        if len(set(source)) != len(source) or len(set(columns)) != len(columns):
            raise ValueError(f"{Path(path).name}: duplicate or colliding columns")
        if any(not name.strip() for name in columns):
            raise ValueError(f"{Path(path).name}: empty column name")
        missing = required - set(columns)
        if missing:
            raise ValueError(f"missing columns: {sorted(missing)}")
        rows = []
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{Path(path).name}: column count mismatch on row {number}")
            rows.append({mapping.get(key, key): value for key, value in row.items()})
    return rows


def _check_export_entries(entries: list[Entry], start_date: str, *, unique_ids: bool = True) -> None:
    """Reject lossy dates, identities and cent-rounded postings before opening an output."""
    dt.date.fromisoformat(start_date)
    seen = set()
    for entry in entries:
        entry.validate_record()
        if unique_ids and entry.entry_id in seen:
            raise ValueError(f"duplicate entry_id {entry.entry_id}")
        seen.add(entry.entry_id)
        to_date(start_date, entry.eff_day)
        to_date(start_date, entry.rec_day)


def write_gl_csv(entries: list[Entry], path: str | Path, start_date: str = "2025-01-01") -> None:
    """One row per posting line (no ground-truth columns)."""
    entries = list(entries)
    _check_export_entries(entries, start_date)
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(GL_COLUMNS)
        for e in entries:
            for ln in e.lines:
                w.writerow(
                    [
                        e.entry_id,
                        to_date(start_date, e.eff_day),
                        to_date(start_date, e.rec_day),
                        ln.account,
                        f"{ln.debit:.2f}",
                        f"{ln.credit:.2f}",
                        e.counterparty or "",
                        e.doc_id or "",
                        e.kind,
                        e.description,
                    ]
                )


def infer_kind(lines) -> str:
    """Classify an entry from its posting lines (Norwegian standard chart NS 4102 numbering).

    ``lines`` is a list of :class:`Line`; a plain set of account numbers is also accepted, in which case
    the direction of the postings is unknown and credit notes cannot be told apart from sales.
    A sale that debits revenue (``3xxx``) is a credit note (``sale_return``); a purchase that debits
    payables (``24xx``) is a supplier credit note (``purchase_return``).
    """
    posted = [ln for ln in lines if isinstance(ln, Line)]
    accounts = {ln.account for ln in posted} if posted else {str(a) for a in lines}

    def has(*prefixes: str) -> bool:
        return any(a.startswith(prefixes) for a in accounts)

    def net_debit(prefix: str) -> float:
        return sum(ln.debit - ln.credit for ln in posted if ln.account.startswith(prefix))

    if has("15") and has("3"):
        return "sale_return" if net_debit("3") > 0 else "sale"
    if has("24") and (has("14") or has("4")):
        return "purchase_return" if net_debit("24") > 0 else "purchase"
    if has("4") and has("14"):
        return "cogs"
    if has("19") and has("15"):
        return "receipt"
    if has("24") and has("19"):
        return "payment"
    if has("19") and has("5", "6", "7"):
        return "expense"
    return "other"


def read_gl_csv(path: str | Path, start_date: str | None = None, mapping: dict[str, str] | None = None) -> list[Entry]:
    """Read posting lines and group them into entries.

    ``mapping`` renames source columns to VeriLedger columns, e.g. ``{"JE_Number": "entry_id"}``.
    Columns ``entry_id``, ``date``, ``account``, ``debit`` and ``credit`` are required; ``record_date``
    defaults to ``date``. Dates are converted to day offsets from ``start_date`` (default: the
    earliest date in the file).
    """
    rows = _csv_rows(path, {"entry_id", "date", "account", "debit", "credit"}, mapping)
    if not rows:
        return []
    start = start_date or min(r["date"][:10] for r in rows)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["entry_id"]].append(r)
    out = []
    for eid, rs in groups.items():
        lines = [Line(r["account"], float(r["debit"] or 0), float(r["credit"] or 0)) for r in rs]
        r0 = rs[0]
        for row in rs[1:]:
            for key in ("date", "record_date", "counterparty", "doc_id", "kind", "description"):

                def value(r, field):
                    v = r.get(field) or (r["date"] if field == "record_date" else "")
                    return v[:10] if field in ("date", "record_date") else v

                if value(row, key) != value(r0, key):
                    raise ValueError(f"entry {eid}: inconsistent {key} across posting lines")
        kind = r0.get("kind") or infer_kind(lines)
        out.append(
            Entry(
                entry_id=eid,
                kind=kind,
                eff_day=to_day(start, r0["date"]),
                rec_day=to_day(start, r0.get("record_date") or r0["date"]),
                lines=lines,
                amount=round(sum(ln.debit for ln in lines), 2),
                counterparty=(r0.get("counterparty") or None),
                doc_id=(r0.get("doc_id") or None),
                description=r0.get("description", ""),
            )
        )
    return out


def add_months(date: str, months: int) -> str:
    """``date`` (YYYY-MM-DD, day 1 recommended) moved by a number of calendar months."""
    import calendar

    if isinstance(months, bool) or not isinstance(months, Integral):
        raise ValueError("months must be an integer")
    d = dt.date.fromisoformat(date[:10])
    y, m = divmod(d.month - 1 + int(months), 12)
    year, month = d.year + y, m + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1])).isoformat()


def fiscal_bounds(start: str, periods: int = 4, months: int = 12) -> list[tuple[int, int]]:
    """Day bounds of ``periods`` equal periods of a fiscal year of ``months`` months from ``start``."""
    integer("periods", periods, 1)
    integer("months", months, 1)
    if months % periods:
        raise ValueError("the number of months must be a multiple of the number of periods")
    step = months // periods
    out = []
    for k in range(periods):
        a = to_day(start, add_months(start, k * step))
        b = to_day(start, add_months(start, (k + 1) * step)) - 1
        out.append((a, b))
    return out


def bounds_between(start: str, end: str, periods: int = 4) -> list[tuple[int, int]]:
    """Day bounds of ``periods`` periods from ``start`` to ``end`` (inclusive, YYYY-MM-DD).

    When both dates are month boundaries the periods follow calendar months, which requires the number
    of months to be a multiple of ``periods``; otherwise the days are split into nearly equal periods."""
    import calendar

    integer("periods", periods, 1)
    a, b = dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10])
    if b < a:
        raise ValueError(f"the period end {end} is before its start {start}")
    if periods < 1:
        raise ValueError("periods must be at least 1")
    days = (b - a).days + 1
    if a.day == 1 and b.day == calendar.monthrange(b.year, b.month)[1]:
        months = (b.year - a.year) * 12 + b.month - a.month + 1
        if months % periods:
            raise ValueError(f"{periods} periods do not divide the {months} months from {start} to {end}")
        return fiscal_bounds(start, periods, months)
    if days < periods:
        raise ValueError(f"{periods} periods do not fit in the {days} days from {start} to {end}")
    edges = [round(k * days / periods) for k in range(periods + 1)]
    return [(edges[k], edges[k + 1] - 1) for k in range(periods)]


# ---------------------------------------------------------------------- SAF-T Financial
def _strip(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    for c in el:
        if _strip(c.tag) == name:
            return c
    return None


def _text(el: ET.Element | None, name: str, default: str = "") -> str:
    if el is None:
        return default
    c = _child(el, name)
    return c.text.strip() if c is not None and c.text else default


def _amount(line: ET.Element, which: str) -> float:
    c = _child(line, which)
    return float(_text(c, "Amount", "0") or 0) if c is not None else 0.0


def saft_selection(path: str | Path) -> tuple[str, str] | None:
    """First and last day covered by a SAF-T file, from its ``SelectionCriteria`` (or ``None``)."""
    root = ET.parse(str(path)).getroot()
    sel = next((el for el in root.iter() if _strip(el.tag) == "SelectionCriteria"), None)
    if sel is None:
        return None
    start, end = _text(sel, "SelectionStartDate"), _text(sel, "SelectionEndDate")
    if start and end:
        return start[:10], end[:10]
    try:
        ys, ms = int(_text(sel, "PeriodStartYear")), int(_text(sel, "PeriodStart"))
        ye, me = int(_text(sel, "PeriodEndYear")), int(_text(sel, "PeriodEnd"))
    except ValueError:
        return None
    first = dt.date(ys, ms, 1)
    last = dt.date.fromisoformat(add_months(dt.date(ye, me, 1).isoformat(), 1)) - dt.timedelta(days=1)
    return first.isoformat(), last.isoformat()


def read_saft(path: str | Path, start_date: str | None = None) -> list[Entry]:
    """Read the general-ledger entries of a SAF-T Financial file."""
    root = ET.parse(str(path)).getroot()
    gle = next((el for el in root.iter() if _strip(el.tag) == "GeneralLedgerEntries"), None)
    if gle is None:
        raise ValueError("no GeneralLedgerEntries section found")
    txs = []  # (journal id, transaction)
    for j in (el for el in gle.iter() if _strip(el.tag) == "Journal"):
        jid = _text(j, "JournalID")
        txs += [(jid, t) for t in j if _strip(t.tag) == "Transaction"]
    if not txs:
        return []
    start = start_date or min(_text(t, "TransactionDate") for _, t in txs)
    raw_ids = [_text(t, "TransactionID") for _, t in txs]
    prefix = len(set(raw_ids)) < len(raw_ids)  # transaction ids repeat across journals: prefix the journal
    seen: set[str] = set()
    out = []
    for jid, t in txs:
        lines, cps, references, documents = [], [], [], []
        for ln in (c for c in t if _strip(c.tag) == "Line"):
            lines.append(Line(_text(ln, "AccountID"), _amount(ln, "DebitAmount"), _amount(ln, "CreditAmount")))
            cps += [x for x in (_text(ln, "CustomerID"), _text(ln, "SupplierID")) if x]
            # ReferenceNumber refers to the invoice; SourceDocumentID often to a voucher or batch
            references += [x for x in (_text(ln, "ReferenceNumber"),) if x]
            documents += [x for x in (_text(ln, "SourceDocumentID"),) if x]
        cps += [x for x in (_text(t, "CustomerID"), _text(t, "SupplierID")) if x]
        distinct_cps = set(cps)
        cp = next(iter(distinct_cps)) if len(distinct_cps) == 1 else ""
        candidates = set(references or documents)
        doc = next(iter(candidates)) if len(candidates) == 1 else ""
        date = _text(t, "TransactionDate")
        entry_date = _text(t, "SystemEntryDate") or _text(t, "GLPostingDate") or date
        kind = infer_kind(lines)
        if kind == "other" and _text(t, "VoucherType") in ("SI", "PI"):
            kind = "sale" if _text(t, "VoucherType") == "SI" else "purchase"
        base = f"{jid}/{_text(t, 'TransactionID')}" if prefix else _text(t, "TransactionID")
        eid, suffix = base, 2
        while eid in seen:
            eid = f"{base}#{suffix}"
            suffix += 1
        seen.add(eid)
        out.append(
            Entry(
                entry_id=eid,
                kind=kind,
                eff_day=to_day(start, date),
                # kept as recorded: an entry date before the transaction date is itself a red flag
                rec_day=to_day(start, entry_date),
                lines=lines,
                amount=round(sum(ln.debit for ln in lines), 2),
                counterparty=cp or None,
                # SourceID identifies the person/application entering the transaction, not its invoice.
                doc_id=doc or None,
                description=_text(t, "Description"),
            )
        )
    return out


def write_saft(
    entries: list[Entry],
    path: str | Path,
    start_date: str = "2025-01-01",
    company: str = "FIRM",
    registration: str = "999999999",
    period_bounds: list[tuple[int, int]] | None = None,
) -> None:
    """Write a minimal SAF-T Financial 1.40 file (header and general-ledger entries).

    With ``period_bounds`` (the fiscal year of an economy) the file covers that fiscal year: the header's
    selection dates are its first and last day, and only entries dated in it are written (entries posted
    after the year end for transactions of the year are included, as in a real year-end file). Without
    it, the selection runs from the first to the last transaction date."""
    entries = list(entries)
    _check_export_entries(entries, start_date, unique_ids=False)
    ET.register_namespace("", SAFT_NS)
    if period_bounds is not None:
        periods(period_bounds)
        first, last = period_bounds[0][0], period_bounds[-1][1]
        entries = [e for e in entries if first <= e.eff_day <= last]
    else:
        days = [e.eff_day for e in entries] or [0]
        first, last = min(days), max(days)

    def sub(parent, tag, text=None):
        el = ET.SubElement(parent, f"{{{SAFT_NS}}}{tag}")
        if text is not None:
            el.text = str(text)
        return el

    root = ET.Element(f"{{{SAFT_NS}}}AuditFile")
    h = sub(root, "Header")
    for tag, val in (
        ("AuditFileVersion", "1.40"),
        ("AuditFileCountry", "NO"),
        ("AuditFileDateCreated", dt.date.today().isoformat()),
        ("SoftwareCompanyName", "VeriLedger"),
        ("SoftwareID", "VeriLedger"),
        ("SoftwareVersion", __version__),
    ):
        sub(h, tag, val)
    c = sub(h, "Company")
    sub(c, "RegistrationNumber", registration)
    sub(c, "Name", company)
    a = sub(c, "Address")
    sub(a, "City", "Oslo")
    sub(a, "PostalCode", "0150")
    ct = sub(c, "Contact")
    cp = sub(ct, "ContactPerson")
    sub(cp, "FirstName", "Simulated")
    sub(cp, "LastName", "Firm")
    sub(h, "DefaultCurrencyCode", "NOK")
    sel = sub(h, "SelectionCriteria")
    sub(sel, "SelectionStartDate", to_date(start_date, first))
    sub(sel, "SelectionEndDate", to_date(start_date, last))
    sub(h, "TaxAccountingBasis", "A")
    gle = sub(root, "GeneralLedgerEntries")
    sub(gle, "NumberOfEntries", len(entries))
    debit = sum(round(line.debit, 2) for entry in entries for line in entry.lines)
    credit = sum(round(line.credit, 2) for entry in entries for line in entry.lines)
    finite("total debit", debit, 0.0)
    finite("total credit", credit, 0.0)
    sub(gle, "TotalDebit", f"{debit:.2f}")
    sub(gle, "TotalCredit", f"{credit:.2f}")
    j = sub(gle, "Journal")
    sub(j, "JournalID", "GL")
    sub(j, "Description", "General journal")
    sub(j, "Type", "GL")
    for e in sorted(entries, key=lambda e: (e.rec_day, e.entry_id)):
        t = sub(j, "Transaction")
        date = to_date(start_date, e.eff_day)
        sub(t, "TransactionID", e.entry_id)
        sub(t, "Period", int(date[5:7]))
        sub(t, "PeriodYear", int(date[:4]))
        sub(t, "TransactionDate", date)
        sub(t, "Description", e.description or e.kind)
        sub(t, "SystemEntryDate", to_date(start_date, e.rec_day))
        sub(t, "GLPostingDate", date)
        for i, ln in enumerate(e.lines, 1):
            line = sub(t, "Line")
            sub(line, "RecordID", i)
            sub(line, "AccountID", ln.account)
            if e.doc_id:
                sub(line, "SourceDocumentID", e.doc_id)
            if e.counterparty:
                sub(
                    line, "CustomerID" if e.kind in ("sale", "receipt", "sale_return") else "SupplierID", e.counterparty
                )
            sub(line, "Description", e.description or e.kind)
            amt = sub(line, "DebitAmount" if ln.debit else "CreditAmount")
            sub(amt, "Amount", f"{(ln.debit or ln.credit):.2f}")
    ET.indent(root)
    ET.ElementTree(root).write(str(path), encoding="UTF-8", xml_declaration=True)


# ---------------------------------------------------------------------- economy bundles
def save_economy(econ: Economy, folder: str | Path, truth: bool = True) -> None:
    """Validate a staged bundle before installing its managed files.

    Input errors leave existing files intact. Unrelated files in ``folder`` are retained.
    ``entries.csv`` holds apparent amounts; ``truth.csv`` retains original recorded amounts and truth.
    """
    if not isinstance(truth, bool):
        raise ValueError("truth must be a boolean")
    if truth:
        econ.require_ground_truth()
    for key, party in econ.parties.items():
        if key != party.party_id:
            raise ValueError("party identifier does not match its dictionary key")
        if not isinstance(party.adopts, bool) or not isinstance(party.related, bool):
            raise ValueError("party adopts and related flags must be booleans")
    for owner, records in econ.mirrors.items():
        if any(record.owner != owner for record in records):
            raise ValueError("mirror owner does not match its counterparty group")
    folder = Path(folder)
    managed = ("entries.csv", "parties.csv", "mirrors.csv", "economy.json", "truth.csv")
    if folder.exists() and not folder.is_dir():
        raise ValueError("economy output must be a directory")
    for name in managed:
        target = folder / name
        if target.exists() and not target.is_file():
            raise ValueError(f"bundle target {name} must be a regular file")
    folder.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".veriledger-bundle-", dir=folder.parent) as temporary:
        pending = Path(temporary)
        _write_economy(econ, pending, truth)
        load_economy(pending)
        folder.mkdir(parents=True, exist_ok=True)
        for name in managed:
            staged = pending / name
            if staged.exists():
                staged.replace(folder / name)
            elif name == "truth.csv":
                (folder / name).unlink(missing_ok=True)


def _write_economy(econ: Economy, folder: str | Path, truth: bool) -> None:
    """Write entries, counterparties, mirror records and (optionally) ground truth to ``folder``.

    ``entries.csv`` is the firm's general ledger as its database now shows it, i.e. after any tampering;
    ``truth.csv`` keeps the amount each entry had when it was posted (``recorded_amount``)."""
    if truth:
        econ.require_ground_truth()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    write_gl_csv(econ.apparent_entries(), folder / "entries.csv", econ.start_date)
    with (folder / "parties.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["party_id", "role", "adopts", "related", "weight"])
        for p in econ.parties.values():
            w.writerow([p.party_id, p.role, int(p.adopts), int(p.related), f"{p.weight:.17g}"])
    with (folder / "mirrors.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["owner", "doc_id", "date", "amount", "kind"])
        for recs in econ.mirrors.values():
            for m in recs:
                w.writerow([m.owner, m.doc_id, to_date(econ.start_date, m.day), f"{m.amount:.2f}", m.kind])
    meta = {
        "firm": econ.firm,
        "start_date": econ.start_date,
        "period_bounds": econ.period_bounds,
        "cost_ratio": econ.cost_ratio,
        "last_day": econ.last_day,
    }
    if truth:
        meta["tamper_days"] = econ.tamper_days
    (folder / "economy.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if not truth:
        (folder / "truth.csv").unlink(missing_ok=True)
    if truth:
        with (folder / "truth.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["entry_id", "misstatement", "true_amount", "true_date", "recorded_amount"])
            for e in econ.entries:
                w.writerow(
                    [
                        e.entry_id,
                        e.misstatement or "",
                        f"{e.true_amount:.2f}",
                        to_date(econ.start_date, e.true_day),
                        f"{e.amount:.2f}",
                    ]
                )


def load_economy(folder: str | Path) -> Economy:
    folder = Path(folder)
    meta = json.loads((folder / "economy.json").read_text(encoding="utf-8"))
    start = meta["start_date"]
    if not isinstance(meta["firm"], str) or not meta["firm"].strip():
        raise ValueError("firm must be a non-empty string")
    if not isinstance(start, str):
        raise ValueError("start_date must be an ISO date string")
    dt.date.fromisoformat(start)
    entries = read_gl_csv(folder / "entries.csv", start_date=start)
    for entry in entries:
        entry.validate_record()
    bounds = [tuple(b) for b in meta["period_bounds"]]
    periods(bounds)
    finite("cost_ratio", meta.get("cost_ratio", 0.6), 0.0, 1.0)
    last_day = meta.get("last_day")
    if last_day is not None:
        integer("last_day", last_day)
    tamper_days = meta.get("tamper_days", {})
    if not isinstance(tamper_days, dict):
        raise ValueError("tamper_days must be a mapping")
    entry_ids = {entry.entry_id for entry in entries}
    if set(tamper_days) - entry_ids:
        raise ValueError("tamper_days refers to unknown entry identifiers")
    for day in tamper_days.values():
        integer("tamper event day", day)
    parties = {}
    for r in _csv_rows(folder / "parties.csv", {"party_id", "role", "adopts", "related", "weight"}):
        if not r["party_id"] or r["party_id"] in parties:
            raise ValueError(f"empty or duplicate party_id {r['party_id']!r}")
        if r["role"] not in ("customer", "supplier"):
            raise ValueError(f"unknown party role {r['role']!r}")
        if r["adopts"] not in ("0", "1") or r["related"] not in ("0", "1"):
            raise ValueError("party adopts and related flags must be 0 or 1")
        weight = float(r["weight"])
        finite("party weight", weight, 0.0)
        parties[r["party_id"]] = Party(r["party_id"], r["role"], r["adopts"] == "1", r["related"] == "1", weight)
    mirrors: dict[str, list[CounterRecord]] = defaultdict(list)
    for r in _csv_rows(folder / "mirrors.csv", {"owner", "doc_id", "date", "amount", "kind"}):
        if r["owner"] not in parties:
            raise ValueError(f"mirror owner {r['owner']!r} is not a known party")
        if r["kind"] not in ("sale", "purchase"):
            raise ValueError(f"unknown mirror kind {r['kind']!r}")
        amount = float(r["amount"])
        finite("mirror amount", amount, 0.0)
        mirrors[r["owner"]].append(CounterRecord(r["owner"], r["doc_id"], to_day(start, r["date"]), amount, r["kind"]))
    tpath = folder / "truth.csv"
    econ = Economy(
        meta["firm"],
        entries,
        parties,
        dict(mirrors),
        bounds,
        start,
        meta.get("cost_ratio", 0.6),
        last_day=meta.get("last_day"),
        tamper_days=meta.get("tamper_days", {}),
        has_ground_truth=tpath.exists(),
    )
    if tpath.exists():
        pos = {e.entry_id: i for i, e in enumerate(entries)}
        seen: set[str] = set()
        required = {"entry_id", "misstatement", "true_amount", "true_date", "recorded_amount"}
        for r in _csv_rows(tpath, required):
            eid = r["entry_id"]
            if eid not in pos:
                raise ValueError(f"truth row refers to unknown entry_id {eid!r}")
            if eid in seen:
                raise ValueError(f"duplicate truth row for entry_id {eid!r}")
            seen.add(eid)
            i = pos[eid]
            e = entries[i]
            if not r["recorded_amount"]:
                raise ValueError(f"missing recorded_amount for entry_id {eid!r}")
            recorded = float(r["recorded_amount"])
            finite("recorded_amount", recorded, 0.0)
            label = r["misstatement"] or None
            if label is not None and label not in SCHEMES:
                raise ValueError(f"unknown misstatement label {label!r}")
            true_amount = float(r["true_amount"])
            finite("true_amount", true_amount, 0.0)
            true_day = to_day(start, r["true_date"])
            if label is None and (
                abs(true_amount - recorded) > 0.005 or true_day != e.eff_day or abs(recorded - e.amount) > 0.005
            ):
                raise ValueError(f"unlabelled truth differs from the recorded entry {eid!r}")
            if abs(recorded - e.amount) > 0.005 and label != "tamper":
                raise ValueError(f"altered recorded_amount requires a tamper label for {eid!r}")
            if abs(recorded - e.amount) > 0.005:  # the database copy was altered after posting
                econ.tampering[e.entry_id] = e.amount
                e = entries[i] = with_amount(e, recorded)
            e.misstatement = label
            e.true_amount = true_amount
            e.true_day = true_day
        if seen != set(pos):
            raise ValueError(f"truth rows missing for {len(set(pos) - seen)} entries")
    return econ
