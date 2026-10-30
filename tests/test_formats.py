import os
from pathlib import Path

import pytest

from veriledger.formats import (
    SAFT_NS,
    bounds_between,
    infer_kind,
    load_economy,
    read_gl_csv,
    read_saft,
    save_economy,
    to_date,
    to_day,
    write_gl_csv,
    write_saft,
)
from veriledger.model import Entry, Line, make_entry  # noqa: E402

SAFT_DIR = os.environ.get("VERILEDGER_SAFT_DIR")  # optional: a clone of github.com/Skatteetaten/saf-t


def test_dates():
    assert to_date("2025-01-01", 31) == "2025-02-01"
    assert to_day("2025-01-01", "2025-02-01T10:00:00") == 31


def test_gl_csv_roundtrip(tmp_path, econ):
    p = tmp_path / "gl.csv"
    write_gl_csv(econ.entries, p, econ.start_date)
    back = read_gl_csv(p, econ.start_date)
    assert [e.record() for e in back] == [e.record() for e in econ.entries]


def test_gl_csv_mapping_and_errors(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("JE,PostDate,Acct,Dr,Cr\nJ1,2025-03-01,1500,10,0\nJ1,2025-03-01,3000,0,10\n")
    m = {"JE": "entry_id", "PostDate": "date", "Acct": "account", "Dr": "debit", "Cr": "credit"}
    (e,) = read_gl_csv(p, mapping=m)
    assert e.kind == "sale" and e.amount == 10 and e.eff_day == 0 and e.is_balanced()
    with pytest.raises(ValueError):
        read_gl_csv(p)


@pytest.mark.parametrize(
    "accounts,kind",
    [
        ({"1500", "3000"}, "sale"),
        ({"4000", "2400", "2710"}, "purchase"),
        ({"1400", "2400"}, "purchase"),
        ({"4000", "1400"}, "cogs"),
        ({"1920", "1500"}, "receipt"),
        ({"2400", "1920"}, "payment"),
        ({"6300", "1920"}, "expense"),
        ({"8800", "2050"}, "other"),
    ],
)
def test_infer_kind(accounts, kind):
    assert infer_kind(accounts) == kind


def test_saft_roundtrip(tmp_path, econ):
    p = tmp_path / "saft.xml"
    ents = econ.entries[:200]
    write_saft(ents, p, econ.start_date)
    back = {e.entry_id: e for e in read_saft(p, econ.start_date)}
    for e in ents:
        b = back[e.entry_id]
        assert (b.kind, b.eff_day, b.rec_day, b.amount, b.counterparty, b.doc_id) == (
            e.kind,
            e.eff_day,
            e.rec_day,
            e.amount,
            e.counterparty,
            e.doc_id,
        )


def test_saft_missing_section(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text('<AuditFile xmlns="urn:StandardAuditFile-Taxation-Financial:NO"><Header/></AuditFile>')
    with pytest.raises(ValueError):
        read_saft(p)


@pytest.mark.skipif(not SAFT_DIR, reason="set VERILEDGER_SAFT_DIR to a clone of the official SAF-T repository")
def test_saft_export_validates_against_official_schema(tmp_path, econ):
    lxml = pytest.importorskip("lxml.etree")
    xsd = Path(SAFT_DIR) / "SAF-T_Financial_1.4" / "Norwegian_SAF-T_Financial_Schema_v_1.40.xsd"
    p = tmp_path / "saft.xml"
    write_saft(econ.entries, p, econ.start_date)
    schema = lxml.XMLSchema(lxml.parse(str(xsd)))
    assert schema.validate(lxml.parse(str(p))), schema.error_log


@pytest.mark.skipif(not SAFT_DIR, reason="set VERILEDGER_SAFT_DIR to a clone of the official SAF-T repository")
def test_official_example_files_import():
    files = [
        f
        for f in Path(SAFT_DIR).rglob("*.xml")
        if f.parent.name == "Example Files"
        and f.name.startswith(("ExampleFile SAF-T Financial", "generated_auditfile"))
    ]
    assert len(files) >= 3
    for f in files:
        ents = read_saft(f)
        assert ents and all(e.is_balanced() for e in ents)


def test_economy_bundle_roundtrip(tmp_path, misstated):
    save_economy(misstated, tmp_path / "eco")
    back = load_economy(tmp_path / "eco")
    assert [e.record() for e in back.entries] == [e.record() for e in misstated.entries]
    assert [(e.misstatement, e.true_amount, e.true_day) for e in back.entries] == [
        (e.misstatement, e.true_amount, e.true_day) for e in misstated.entries
    ]
    assert back.tampering == misstated.tampering
    assert back.adopters() == misstated.adopters()
    assert sum(len(v) for v in back.mirrors.values()) == sum(len(v) for v in misstated.mirrors.values())


def test_bundle_without_truth(tmp_path, misstated):
    save_economy(misstated, tmp_path / "eco", truth=False)
    assert not (tmp_path / "eco" / "truth.csv").exists()
    assert not load_economy(tmp_path / "eco").misstated()


def test_saved_entries_are_the_altered_database(tmp_path, misstated):
    import csv

    save_economy(misstated, tmp_path / "b")
    amounts = {}
    with (tmp_path / "b" / "entries.csv").open() as f:
        for r in csv.DictReader(f):
            amounts[r["entry_id"]] = amounts.get(r["entry_id"], 0.0) + float(r["debit"])
    for eid, altered in misstated.tampering.items():
        assert amounts[eid] == pytest.approx(altered)
    back = load_economy(tmp_path / "b")
    orig = {e.entry_id: e.amount for e in misstated.entries}
    assert all(e.amount == pytest.approx(orig[e.entry_id]) for e in back.entries)


@pytest.mark.parametrize(
    "lines,kind",
    [
        ([Line("1500", debit=10), Line("3000", credit=10)], "sale"),
        ([Line("3000", debit=10), Line("1500", credit=10)], "sale_return"),
        ([Line("4000", debit=8), Line("2710", debit=2), Line("2400", credit=10)], "purchase"),
        ([Line("2400", debit=10), Line("4000", credit=8), Line("2710", credit=2)], "purchase_return"),
    ],
)
def test_infer_kind_uses_direction(lines, kind):
    assert infer_kind(lines) == kind


def test_saft_credit_note_and_zero_entry(tmp_path):
    from veriledger.ledger import Ledger

    cn = Entry("CN1", "x", 10, 12, [Line("3000", debit=500.0), Line("1500", credit=500.0)], 500.0, "C001", "CN-1")
    zero = Entry("Z1", "x", 3, 3, [Line("1920", debit=0.0), Line("6300", credit=0.0)], 0.0)
    write_saft([cn, zero], tmp_path / "f.xml")
    got = {e.entry_id: e for e in read_saft(tmp_path / "f.xml")}
    assert got["CN1"].kind == "sale_return"
    assert got["Z1"].amount == 0 and got["Z1"].is_balanced()
    Ledger.from_entries("F", got.values(), [(0, 89)])  # zero-amount entries are accepted


def test_saft_keeps_entry_dates_before_transaction_dates(tmp_path):
    e = make_entry("T1", "sale", 20, 100.0, rec_day=18, counterparty="C001", doc_id="INV-1")
    write_saft([e], tmp_path / "f.xml")
    got = read_saft(tmp_path / "f.xml", start_date="2025-01-01")[0]
    assert (got.eff_day, got.rec_day) == (20, 18)


def test_saft_duplicate_transaction_ids_across_journals(tmp_path):
    import copy
    import xml.etree.ElementTree as ET

    write_saft([make_entry("1", "sale", 1, 10.0), make_entry("2", "expense", 2, 5.0)], tmp_path / "f.xml")
    tree = ET.parse(tmp_path / "f.xml")
    ns = {"n": SAFT_NS}
    gle = tree.getroot().find("n:GeneralLedgerEntries", ns)
    j2 = copy.deepcopy(gle.find("n:Journal", ns))
    j2.find("n:JournalID", ns).text = "SALES"
    gle.append(j2)
    tree.write(tmp_path / "g.xml")
    ids = [e.entry_id for e in read_saft(tmp_path / "g.xml")]
    assert len(ids) == len(set(ids)) == 4 and "SALES/1" in ids and "GL/1" in ids


def test_fiscal_bounds():
    from veriledger.formats import add_months, fiscal_bounds

    assert fiscal_bounds("2025-01-01", 4) == [(0, 89), (90, 180), (181, 272), (273, 364)]
    assert fiscal_bounds("2024-07-01", 2)[-1][1] == 364
    assert add_months("2025-01-31", 1) == "2025-02-28"
    with pytest.raises(ValueError):
        fiscal_bounds("2025-01-01", 5)


def test_cli_check_saft(tmp_path, misstated, capsys):
    import csv
    import json

    from veriledger.cli import main

    bounds = misstated.period_bounds
    write_saft(misstated.apparent_entries(), tmp_path / "f.xml", misstated.start_date, period_bounds=bounds)
    assert main(["check", str(tmp_path / "f.xml"), "--out", str(tmp_path / "flags.csv")]) == 0
    out = json.loads(capsys.readouterr().out)
    # the header carries the economy's fiscal year, and only its entries are exported
    assert out["fiscal_year"] == [misstated.start_date, to_date(misstated.start_date, misstated.fiscal_end)]
    assert out["recorded"] == len(misstated.year_entries())
    # periods follow calendar months when the fiscal year starts and ends on month boundaries
    expected = bounds_between(misstated.start_date, to_date(misstated.start_date, misstated.fiscal_end), 4)
    assert [tuple(to_day(misstated.start_date, d) for d in p) for p in out["periods"]] == expected
    assert out["cutoff_flags"] > 0 and out["duplicate_flags"] > 0
    rows = list(csv.DictReader((tmp_path / "flags.csv").open()))
    dup_docs = [r for r in rows if "duplicate" in r["flags"]]
    assert len(dup_docs) == out["duplicate_flags"]


def test_cli_check_csv(tmp_path, misstated, capsys):
    import json

    from veriledger.cli import main

    write_gl_csv(misstated.entries, tmp_path / "gl.csv", misstated.start_date)
    assert main(["check", str(tmp_path / "gl.csv"), "--start", "2025-01-01", "--periods", "12"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["periods"]) == 12 and out["unbalanced_skipped"] == []


@pytest.mark.skipif(not SAFT_DIR, reason="set VERILEDGER_SAFT_DIR to a clone of the official SAF-T repository")
def test_cli_check_official_example_files(capsys):
    import json

    from veriledger.cli import main

    files = [
        f
        for f in Path(SAFT_DIR).rglob("*.xml")
        if f.parent.name == "Example Files" and f.name.startswith("ExampleFile SAF-T Financial")
    ]
    for f in files:
        assert main(["check", str(f)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["recorded"] == out["entries"] - len(out["unbalanced_skipped"]) - out["before_start_skipped"]
