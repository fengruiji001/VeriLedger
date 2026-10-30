"""Tests for dated evidence, lossless bundles and export boundaries."""

import csv
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from veriledger.audit import DetectionModel
from veriledger.cli import main
from veriledger.formats import (
    SAFT_NS,
    add_months,
    bounds_between,
    fiscal_bounds,
    load_economy,
    save_economy,
    to_date,
    write_gl_csv,
    write_saft,
)
from veriledger.lending import lend
from veriledger.model import CounterRecord, Economy, Entry, Line, Party, make_entry
from veriledger.synth import EconomyConfig, generate, period_bounds

BOUNDS = [(0, 4), (5, 9)]


def example():
    e = make_entry("A", "sale", 1, 100, counterparty="C", doc_id="I")
    return Economy("F", [e], {"C": Party("C", "customer")}, {"C": [CounterRecord("C", "I", 1, 100, "sale")]}, BOUNDS)


@pytest.mark.parametrize("peer_amount", [100, 200])
def test_lending_current_pending_exception_overrides_old_clearance(peer_amount):
    a = make_entry("A", "purchase", 4, 100, rec_day=3, counterparty="S", doc_id="I")
    a.misstatement = "tamper"
    e = Economy(
        "F",
        [a],
        {"S": Party("S", "supplier", adopts=True)},
        {"S": [CounterRecord("S", "I", 4, peer_amount, "purchase")]},
        BOUNDS,
        tampering={"A": 200},
        tamper_days={"A": 8},
    )
    r = lend(e, "ledger", opening_days=0, detection=DetectionModel(investigate={"tamper": 0}))
    assert r.audit["unresolved"] == 1
    assert r.audit["investigated"] == 2 and r.audit["false_positives"] == 1
    assert r.audit["matched"] == int(peer_amount == 200)
    assert r.audit["unmatched"] == int(peer_amount == 100)
    assert r.verified_share == pytest.approx(0.5)
    assert r.credit == pytest.approx(90)


@pytest.mark.parametrize(
    "writer,problem",
    [(writer, problem) for writer in (write_gl_csv, write_saft) for problem in ("date", "amount", "cents")]
    + [(write_gl_csv, "duplicate_id")],
)
def test_exports_validate_every_entry_before_overwriting(writer, problem, tmp_path):
    a = make_entry("A", "sale", 1, 100)
    entries = [a]
    if problem == "date":
        a.rec_day = 1.9
    elif problem == "amount":
        a.amount = float("nan")
    elif problem == "cents":
        entries = [
            Entry(
                "A",
                "sale",
                1,
                1,
                [Line("1500", debit=0.006), Line("1501", debit=0.006), Line("3000", credit=0.012)],
                0.012,
            )
        ]
    else:
        entries.append(a.copy())
    path = tmp_path / "existing"
    path.write_bytes(b"original data")
    with pytest.raises(ValueError):
        writer(entries, path)
    assert path.read_bytes() == b"original data"


def test_saft_header_totals_equal_the_serialized_posting_lines(tmp_path):
    entries = [
        Entry(str(i), "sale", i, i, [Line("1500", debit=1.004), Line("3000", credit=1.004)], 1.004) for i in (1, 2)
    ]
    path = tmp_path / "cents.xml"
    write_saft(entries, path)
    gle = ET.parse(path).getroot().find(f"{{{SAFT_NS}}}GeneralLedgerEntries")
    assert float(gle.findtext(f"{{{SAFT_NS}}}TotalDebit")) == 2
    assert float(gle.findtext(f"{{{SAFT_NS}}}TotalCredit")) == 2


@pytest.mark.parametrize(
    "problem,truth",
    [
        ("last_day", True),
        ("last_day", False),
        ("true_amount", True),
        ("role", True),
        ("adopts", True),
        ("owner", True),
        ("event", True),
    ],
)
def test_invalid_bundle_save_preserves_existing_files(problem, truth, tmp_path):
    e = example()
    folder = tmp_path / "economy"
    save_economy(e, folder)
    (folder / "keep.txt").write_text("unrelated file")
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    if problem == "last_day":
        e.last_day = True
    elif problem == "true_amount":
        e.entries[0].true_amount = float("nan")
    elif problem == "role":
        e.parties["C"].role = "unknown"
    elif problem == "adopts":
        e.parties["C"].adopts = 0.5
    elif problem == "owner":
        e.mirrors["WRONG"] = e.mirrors.pop("C")
    else:
        e.tamper_days["UNKNOWN"] = 3
    with pytest.raises(ValueError):
        save_economy(e, folder, truth=truth)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert load_economy(folder).entries[0].amount == 100


@pytest.mark.parametrize("weight", [1e-9, 0.12345678901234567])
def test_bundle_preserves_counterparty_weight_precision(weight, tmp_path):
    e = example()
    e.parties["C"].weight = weight
    save_economy(e, tmp_path)
    assert load_economy(tmp_path).parties["C"].weight == weight


@pytest.mark.parametrize("exponent", [10, 20])
def test_integer_zipf_exponents_generate_valid_probabilities(exponent):
    options = dict(days=10, periods=2, post_days=0, n_customers=100, n_suppliers=5, related_parties=0)
    a = generate(EconomyConfig(**options, customer_zipf=exponent), 0)
    b = generate(EconomyConfig(**options, customer_zipf=float(exponent)), 0)
    weights = [p.weight for p in a.parties.values() if p.role == "customer"]
    assert np.isfinite(weights).all() and min(weights) >= 0
    assert sum(weights) == pytest.approx(1)
    assert [e.record() for e in a.entries] == [e.record() for e in b.entries]


@pytest.mark.parametrize(
    "function,args,kwargs",
    [
        (to_date, ("2025-01-01", 1.5), {}),
        (to_date, ("2025-01-01", True), {}),
        (to_date, ("2025-01-01", "2"), {}),
        (to_date, ("2025-01-01", 10**20), {}),
        (fiscal_bounds, ("2025-01-01",), {"months": 0}),
        (fiscal_bounds, ("2025-01-01",), {"months": -4}),
        (fiscal_bounds, ("2025-01-01",), {"periods": True}),
        (bounds_between, ("2025-01-01", "2025-12-31"), {"periods": True}),
        (add_months, ("2025-01-01", True), {}),
        (period_bounds, (3, 4), {}),
    ],
)
def test_calendar_helpers_reject_invalid_discrete_inputs(function, args, kwargs):
    with pytest.raises(ValueError):
        function(*args, **kwargs)


@pytest.mark.parametrize("selection", [False, True])
def test_cli_imports_empty_saft_as_header_only_csv(selection, tmp_path, capsys):
    path = tmp_path / "empty.xml"
    write_saft([], path)
    if not selection:
        tree = ET.parse(path)
        header = tree.getroot().find(f"{{{SAFT_NS}}}Header")
        header.remove(header.find(f"{{{SAFT_NS}}}SelectionCriteria"))
        tree.write(path)
    out = tmp_path / "empty.csv"
    assert main(["saft-import", str(path), "--out", str(out)]) == 0
    with out.open() as f:
        rows = list(csv.reader(f))
    assert len(rows) == 1 and "entry_id" in rows[0]
    assert "0 transactions" in capsys.readouterr().out


def test_cli_saft_import_reports_malformed_input_without_overwriting(tmp_path, capsys):
    path, out = tmp_path / "bad.xml", tmp_path / "old.csv"
    path.write_text("<broken")
    out.write_text("original data")
    assert main(["saft-import", str(path), "--out", str(out)]) == 2
    assert out.read_text() == "original data"
    assert "error:" in capsys.readouterr().err


def test_blocked_bundle_target_does_not_overwrite_other_files(tmp_path):
    e = example()
    save_economy(e, tmp_path)
    original = (tmp_path / "entries.csv").read_bytes()
    (tmp_path / "parties.csv").unlink()
    (tmp_path / "parties.csv").mkdir()
    e.entries[0] = make_entry("A", "sale", 1, 200, counterparty="C", doc_id="I")
    e.mirrors["C"] = [CounterRecord("C", "I", 1, 200, "sale")]
    with pytest.raises((OSError, ValueError)):
        save_economy(e, tmp_path)
    assert (tmp_path / "entries.csv").read_bytes() == original
