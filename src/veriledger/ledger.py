"""Append-only, hash-chained ledger with per-period Merkle commitments.

Each entry is stored as a canonical record. Its leaf hash is ``H(0x00 || record)`` and the chain hash
is ``c_i = H(c_{i-1} || leaf_i)``. When a period closes (``end + close_lag`` days), the ledger publishes
a commitment: the Merkle root of the leaves recorded for that period. Entries recorded for an already
committed period are kept but marked *late*. Replicated leaf hashes stand for the copies held by other
nodes, so later modification of the firm's own records is detectable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from .merkle import MerkleTree, Proof, leaf_hash, sha256, verify_proof
from .model import Entry, Line, with_amount
from .validation import has_document_id, integer, periods


@dataclass(frozen=True)
class Commitment:
    period: int
    root: bytes
    size: int
    commit_day: int
    chain_head: bytes
    n_records: int  # number of ledger records when the commitment was made
    context_hash: bytes = b""  # binds the firm, fiscal periods, close lag and file format

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "root": self.root.hex(),
            "size": self.size,
            "commit_day": self.commit_day,
            "chain_head": self.chain_head.hex(),
            "n_records": self.n_records,
            "context_hash": self.context_hash.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Commitment:
        return cls(
            d["period"],
            bytes.fromhex(d["root"]),
            d["size"],
            d["commit_day"],
            bytes.fromhex(d["chain_head"]),
            d["n_records"],
            bytes.fromhex(d.get("context_hash", "")),
        )


@dataclass
class IntegrityReport:
    """Result of :meth:`Ledger.verify`."""

    n_records: int
    modified: list[int] = field(default_factory=list)  # indices whose record no longer matches the replica
    chain_ok: bool = True
    bad_periods: list[int] = field(default_factory=list)
    missing_records: int = 0  # records covered by a commitment but absent from the firm's copy

    metadata_ok: bool = True
    commitment_coverage: bool = True
    uncovered_records: int = 0
    unanchored_periods: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.modified
            and self.chain_ok
            and not self.bad_periods
            and not self.missing_records
            and self.metadata_ok
            and self.commitment_coverage
            and not self.unanchored_periods
            and not self.errors
        )


def canonical(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


class Ledger:
    """Hash-chained ledger of one firm."""

    def __init__(self, firm: str, period_bounds: list[tuple[int, int]], close_lag: int = 5):
        if not isinstance(firm, str) or not firm.strip():
            raise ValueError("firm must be a non-empty string")
        periods(period_bounds)
        integer("close_lag", close_lag)
        self.format_version = 2
        self.firm = firm
        self.period_bounds = list(period_bounds)
        self.close_lag = int(close_lag)
        self.genesis = sha256(b"veriledger-genesis:" + firm.encode("utf-8"))
        self.records: list[dict] = []  # the firm's own copy (can be tampered with)
        self.leaves: list[bytes] = []  # replicated leaf hashes
        self.chain: list[bytes] = []  # replicated chain hashes
        self.index: dict[str, int] = {}
        # period index -> record indices; index len(period_bounds) holds records of the following year
        self.members: dict[int, list[int]] = {k: [] for k in range(len(period_bounds) + 1)}
        self.late: set[int] = set()
        self.commits: dict[int, Commitment] = {}
        self._trees: dict[int, MerkleTree] = {}
        self._pos: dict[int, dict[int, int]] = {}  # period -> record index -> leaf position

    @property
    def context_hash(self) -> bytes:
        return sha256(
            canonical(
                {
                    "format_version": self.format_version,
                    "firm": self.firm,
                    "period_bounds": self.period_bounds,
                    "close_lag": self.close_lag,
                }
            )
        )

    # ------------------------------------------------------------------ construction
    def period_of(self, day: int) -> int:
        """Period index of ``day``; ``len(period_bounds)`` for days after the last period (the following
        year, which this ledger does not commit)."""
        for k, (a, b) in enumerate(self.period_bounds):
            if a <= day <= b:
                return k
        return 0 if day < self.period_bounds[0][0] else len(self.period_bounds)

    def commit_day(self, k: int) -> int:
        return self.period_bounds[k][1] + self.close_lag

    def _commit_due(self, day: int) -> None:
        for k in range(len(self.period_bounds)):
            if k not in self.commits and self.commit_day(k) < day:
                self.commit(k)

    def append(self, entry: Entry) -> int:
        """Append a balanced entry; ``rec_day`` must not decrease. Returns the record index."""
        entry.validate_record()
        if self.records and entry.rec_day < self.records[-1]["rec_day"]:
            raise ValueError("append-only ledger: rec_day must not decrease")
        if entry.entry_id in self.index:
            raise ValueError(f"duplicate entry_id {entry.entry_id}")
        self._commit_due(entry.rec_day)
        rec = entry.record()
        leaf = leaf_hash(canonical(rec))
        prev = self.chain[-1] if self.chain else self.genesis
        i = len(self.records)
        self.records.append(rec)
        self.leaves.append(leaf)
        self.chain.append(sha256(prev + leaf))
        self.index[entry.entry_id] = i
        k = self.period_of(entry.eff_day)
        if k < len(self.period_bounds) and k in self.commits:
            self.late.add(i)
        else:
            self.members[k].append(i)
        return i

    def commit(self, k: int) -> Commitment:
        if k in self.commits:
            return self.commits[k]
        tree = MerkleTree([self.leaves[i] for i in self.members[k]])
        head = self.chain[-1] if self.chain else self.genesis
        c = Commitment(k, tree.root, tree.size, self.commit_day(k), head, len(self.records), self.context_hash)
        self.commits[k] = c
        self._index_tree(k, tree)
        return c

    def _index_tree(self, k: int, tree: MerkleTree) -> None:
        self._trees[k] = tree
        self._pos[k] = {i: j for j, i in enumerate(self.members[k])}

    def close(self) -> None:
        """Commit all periods that are not yet committed, then publish a closing checkpoint so that every
        record is covered by a commitment (see :meth:`checkpoint`)."""
        for k in range(len(self.period_bounds)):
            self.commit(k)
        self.checkpoint()

    def checkpoint(self) -> Commitment | None:
        """Commit the records posted after the last commitment: records of the following year (under
        period index ``len(period_bounds)``, with inclusion proofs) and late records (covered by the chain
        head). Replaces an earlier checkpoint; returns ``None`` when nothing is uncovered."""
        k = len(self.period_bounds)
        covered = max((c.n_records for c in self.commits.values()), default=0)
        if len(self.records) <= covered and (k in self.commits or not self.members[k]):
            return None
        tree = MerkleTree([self.leaves[i] for i in self.members[k]])
        c = Commitment(
            k,
            tree.root,
            tree.size,
            int(self.records[-1]["rec_day"]),
            self.chain[-1],
            len(self.records),
            self.context_hash,
        )
        self.commits[k] = c
        self._index_tree(k, tree)
        return c

    @classmethod
    def from_entries(
        cls, firm: str, entries: Iterable[Entry], period_bounds: list[tuple[int, int]], close_lag: int = 5
    ) -> Ledger:
        led = cls(firm, period_bounds, close_lag)
        for e in sorted(entries, key=lambda e: (e.rec_day, e.entry_id)):
            led.append(e)
        led.close()
        return led

    # ------------------------------------------------------------------ proofs
    def prove(self, entry_id: str) -> tuple[int, Proof]:
        """Inclusion proof of an entry in its period commitment (late entries have none)."""
        i = self.index[entry_id]
        if i in self.late:
            raise KeyError(f"{entry_id} was recorded after its period was committed")
        k = self.period_of(self.records[i]["eff_day"])
        if k not in self.commits:
            raise KeyError(f"period {k} is not committed (yet)")
        return k, self._trees[k].proof(self._pos[k][i])

    def verify_inclusion(
        self, record: dict, period: int, proof: Proof, *, commitment: Commitment | None = None
    ) -> bool:
        """Check a disclosed record against an explicitly supplied commitment.

        Callers must obtain the commitment from a trustworthy source; the ledger's adjacent file
        cannot establish its own independence. Pass ``commitment=ledger.commits[period]`` only for
        an explicitly local consistency check. The tree size is taken from the supplied commitment.
        """
        if commitment is None:
            raise ValueError("supply an explicit commitment for inclusion verification")
        c = commitment
        if not isinstance(c, Commitment) or not isinstance(proof, Proof):
            return False
        try:
            for name in ("period", "size", "n_records", "commit_day"):
                integer(f"commitment {name}", getattr(c, name))
        except (TypeError, ValueError):
            return False
        if any(not isinstance(value, bytes) or len(value) != 32 for value in (c.root, c.chain_head, c.context_hash)):
            return False
        if c.size > c.n_records:
            return False
        if 0 <= c.period < len(self.period_bounds) and c.commit_day != self.commit_day(c.period):
            return False
        return (
            isinstance(period, int)
            and not isinstance(period, bool)
            and 0 <= period <= len(self.period_bounds)
            and c.period == period
            and c.context_hash == self.context_hash
            and proof.size == c.size
            and verify_proof(leaf_hash(canonical(record)), proof, c.root)
        )

    # ------------------------------------------------------------------ checks
    def tamper(self, entry_id: str, **changes) -> None:
        """Modify the firm's copy; amount edits rescale valid two-line postings atomically.

        Raw changes to other fields can simulate malformed records. Unsupported or invalid amount
        edits raise before any field is changed; zero-value standard entries retain account direction.
        """
        rec = self.records[self.index[entry_id]]
        candidate = {**rec, **changes}
        if "amount" in changes:
            entry = Entry(**{**rec, "lines": [Line(*line) for line in rec["lines"]]})
            adjusted = with_amount(entry, changes["amount"]).record()
            candidate["amount"], candidate["lines"] = adjusted["amount"], adjusted["lines"]
        rec.update(candidate)

    def verify(self, commitments: dict[int, Commitment] | None = None) -> IntegrityReport:
        """Recompute hashes from the firm's records and compare them with the replicated hashes and with
        the published commitments (``commitments`` defaults to the ledger's own)."""
        external = commitments is not None
        commitments = self.commits if commitments is None else commitments
        rep = IntegrityReport(len(self.records))
        if not commitments:
            rep.commitment_coverage = False
            rep.errors.append("no independent commitments" if external else "no commitments")
        prev = self.genesis
        stored_chain_ok = len(self.chain) == len(self.records)
        leaves, chain = [], []
        seen_ids: set[str] = set()
        last_recording_day: int | None = None
        for i, rec in enumerate(self.records):
            try:
                entry = Entry(
                    entry_id=rec["entry_id"],
                    kind=rec["kind"],
                    eff_day=rec["eff_day"],
                    rec_day=rec["rec_day"],
                    lines=[Line(*line) for line in rec["lines"]],
                    amount=rec["amount"],
                    counterparty=rec["counterparty"],
                    doc_id=rec["doc_id"],
                    description=rec["description"],
                )
                entry.validate_record()
                if entry.entry_id in seen_ids:
                    raise ValueError("duplicate entry identifier")
                if last_recording_day is not None and entry.rec_day < last_recording_day:
                    raise ValueError("recording days are not in append order")
                seen_ids.add(entry.entry_id)
                last_recording_day = entry.rec_day
            except (ValueError, TypeError, KeyError) as exc:
                rep.errors.append(f"invalid record at index {i}: {exc}")
            encoding_ok = True
            try:
                leaf = leaf_hash(canonical(rec))
            except (TypeError, ValueError, OverflowError, RecursionError) as exc:
                rep.errors.append(f"non-serializable record at index {i}: {exc}")
                # Continue checking other records with a placeholder, never a valid encoding.
                leaf = bytes(32)
                encoding_ok = stored_chain_ok = False
            leaves.append(leaf)
            if not encoding_ok or i >= len(self.leaves) or leaf != self.leaves[i]:
                rep.modified.append(i)
            prev = sha256(prev + leaf)
            chain.append(prev)
            if i >= len(self.chain) or prev != self.chain[i]:
                stored_chain_ok = False
        actual_head = chain[-1] if chain else self.genesis
        stored_head = self.chain[-1] if self.chain else self.genesis
        rep.chain_ok = stored_chain_ok and actual_head == stored_head
        n = len(self.records)
        covered = 0
        for k, c in sorted(commitments.items()):
            bad = False
            if c.context_hash != self.context_hash:
                rep.metadata_ok = False
                bad = True
            try:
                integer("commitment period", c.period)
                integer("commitment size", c.size)
                integer("commitment record count", c.n_records)
                integer("commit day", c.commit_day)
                if c.period != k or k > len(self.period_bounds) or c.size > c.n_records:
                    raise ValueError("inconsistent commitment fields")
            except (ValueError, TypeError) as exc:
                rep.errors.append(str(exc))
                rep.bad_periods.append(k)
                continue
            covered = max(covered, min(c.n_records, n))
            if c.n_records > n:
                rep.bad_periods.append(k)
                rep.missing_records = max(rep.missing_records, c.n_records - n)
                continue
            head = chain[c.n_records - 1] if c.n_records else self.genesis
            members = [i for i in self.members.get(k, []) if i < c.n_records]
            root = MerkleTree([leaves[i] for i in members]).root
            if k < len(self.period_bounds) and c.commit_day != self.commit_day(k):
                bad = True
            if root != c.root or head != c.chain_head or len(members) != c.size:
                bad = True
            if bad:
                rep.bad_periods.append(k)
        # ``late`` is stored outside the hashed record.  When an independent
        # period commitment is available, its covered prefix determines
        # whether each fiscal-period entry arrived after that commitment.
        required_periods: set[int] = set()
        for i, rec in enumerate(self.records):
            if not isinstance(rec, dict):
                continue  # already reported above; malformed records cannot establish a fiscal period
            if not isinstance(rec.get("eff_day"), int) or isinstance(rec.get("eff_day"), bool):
                continue
            k = self.period_of(rec["eff_day"])
            period_commitment = commitments.get(k)
            if k < len(self.period_bounds):
                required_periods.add(k)
            if (
                k < len(self.period_bounds)
                and period_commitment is not None
                and isinstance(period_commitment.n_records, int)
                and period_commitment.n_records >= 0
            ):
                if (i in self.late) != (i >= period_commitment.n_records):
                    rep.errors.append(f"late-record classification mismatch at index {i}")
        rep.unanchored_periods = sorted(required_periods - set(commitments))
        if rep.unanchored_periods:
            rep.errors.append("period commitments missing for late-record verification")
        if any(i < 0 or i >= n for i in self.late):
            rep.errors.append("late-record index outside the ledger")
        rep.uncovered_records = n - covered
        rep.commitment_coverage = bool(commitments) and not rep.uncovered_records
        if rep.bad_periods:
            rep.chain_ok = False
        return rep

    def cutoff_flags(self) -> list[int]:
        """Records posted after the end of the period they claim to belong to."""
        out = []
        for i, rec in enumerate(self.records):
            k = self.period_of(rec["eff_day"])
            if k < len(self.period_bounds) and rec["rec_day"] > self.period_bounds[k][1]:
                out.append(i)
        return out

    def forward_flags(self) -> list[int]:
        """Records entered in the system before the transaction date they carry (``rec_day < eff_day``)."""
        return [i for i, rec in enumerate(self.records) if rec["rec_day"] < rec["eff_day"]]

    def duplicate_flags(self, kinds: tuple[str, ...] = ("sale", "purchase")) -> list[int]:
        """Records that repeat the document id of an earlier record of the same kind and counterparty
        (supplier invoice numbers are unique only per supplier)."""
        seen: set[tuple[str, str, str]] = set()
        out = []
        for i, rec in enumerate(self.records):
            if rec["kind"] in kinds and has_document_id(rec["doc_id"]):
                key = (rec["kind"], rec["counterparty"] or "", rec["doc_id"])
                if key in seen:
                    out.append(i)
                seen.add(key)
        return out

    def entry_ids(self, idx: Iterable[int]) -> set[str]:
        return {self.records[i]["entry_id"] for i in idx}

    # ------------------------------------------------------------------ persistence
    def save(self, path: str | Path) -> None:
        """Stage JSON Lines and its commitment sidecar before replacing either target.

        Serialization and non-file target errors preserve existing files. Replacement is not a
        filesystem transaction spanning both files. Serializable damaged records remain writable.
        """
        path = Path(path)
        sidecar = Path(str(path) + ".commits.json")
        for target in (path, sidecar):
            if target.exists() and not target.is_file():
                raise ValueError(f"ledger target {target.name} must be a regular file")
        rows = [
            json.dumps(
                {
                    "format_version": self.format_version,
                    "firm": self.firm,
                    "period_bounds": self.period_bounds,
                    "close_lag": self.close_lag,
                }
            )
        ]
        for i, rec in enumerate(self.records):
            rows.append(
                json.dumps(
                    {
                        "record": rec,
                        "leaf": self.leaves[i].hex(),
                        "chain": self.chain[i].hex(),
                        "late": i in self.late,
                    }
                )
            )
        commits = [c.to_dict() for _, c in sorted(self.commits.items())]
        payload, commitment_payload = "\n".join(rows) + "\n", json.dumps(commits, indent=1)
        with TemporaryDirectory(prefix=".veriledger-ledger-", dir=path.parent) as temporary:
            staged = Path(temporary)
            (staged / "ledger.jsonl").write_text(payload, encoding="utf-8")
            (staged / "commits.json").write_text(commitment_payload, encoding="utf-8")
            (staged / "ledger.jsonl").replace(path)
            (staged / "commits.json").replace(sidecar)

    @classmethod
    def load(cls, path: str | Path) -> Ledger:
        path = Path(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        head = json.loads(lines[0])
        led = cls(head["firm"], [tuple(b) for b in head["period_bounds"]], head["close_lag"])
        led.format_version = head.get("format_version", 1)
        if led.format_version not in (1, 2):
            raise ValueError("unsupported ledger format version")
        for i, line in enumerate(lines[1:]):
            d = json.loads(line)
            rec = d["record"]
            if rec["entry_id"] in led.index:
                raise ValueError(f"duplicate entry_id {rec['entry_id']}")
            if not isinstance(d["late"], bool):
                raise ValueError("late must be a boolean")
            led.records.append(rec)
            led.leaves.append(bytes.fromhex(d["leaf"]))
            led.chain.append(bytes.fromhex(d["chain"]))
            led.index[rec["entry_id"]] = i
            if d["late"]:
                led.late.add(i)
            else:
                led.members[led.period_of(rec["eff_day"])].append(i)
        cpath = Path(str(path) + ".commits.json")
        if cpath.exists():
            for d in json.loads(cpath.read_text(encoding="utf-8")):
                c = Commitment.from_dict(d)
                if c.period in led.commits:
                    raise ValueError("duplicate commitment period")
                led.commits[c.period] = c
                led._index_tree(c.period, MerkleTree([led.leaves[i] for i in led.members[c.period]]))
        return led
