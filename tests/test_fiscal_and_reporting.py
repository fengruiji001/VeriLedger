"""Tests for calendar fiscal years, structural realism, both opinion error types,
consistent SAF-T export and checks."""

import collections
import json

import pytest
from conftest import SMALL

from veriledger.audit import AuditResult, SamplingAudit
from veriledger.cli import main
from veriledger.formats import (
    bounds_between,
    load_economy,
    read_saft,
    saft_selection,
    save_economy,
    to_date,
    write_saft,
)
from veriledger.metrics import evaluate
from veriledger.misstate import inject
from veriledger.model import Entry, Line
from veriledger.synth import EconomyConfig, generate

MIS = dict(backdate=0.3, fictitious=0.01, inflate=0.01, duplicate=0.005, round_trip=4, tamper=0.005)


@pytest.fixture(scope="module")
def default_economy():
    return inject(generate(EconomyConfig(adoption=0.5), seed=7), MIS, seed=8)


# ---------------------------------------------------------------- calendar fiscal year
def test_default_fiscal_year_is_the_calendar_year():
    e = generate(EconomyConfig(sales_per_day=1, purchases_per_day=1, expenses_per_day=0), seed=1)
    ends = [to_date(e.start_date, b) for _, b in e.period_bounds]
    assert ends == ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    assert e.fiscal_end == 364 and e.last_day == 364 + 30
    leap = generate(EconomyConfig(start_date="2024-01-01", periods=12, sales_per_day=1), seed=1)
    assert leap.fiscal_end == 365 and len(leap.period_bounds) == 12
    odd = generate(EconomyConfig(periods=5, sales_per_day=1), seed=1)  # 5 does not divide 12: equal periods
    assert odd.period_bounds[-1][1] == 364 and len(odd.period_bounds) == 5


def test_explicit_days_keep_equal_periods():
    e = generate(EconomyConfig(**SMALL), seed=1)
    assert e.period_bounds == [(0, 29), (30, 59), (60, 89), (90, 119)]


def test_bounds_between():
    assert bounds_between("2025-01-01", "2025-06-30", 2) == [(0, 89), (90, 180)]
    assert bounds_between("2025-01-05", "2025-01-14", 2) == [(0, 4), (5, 9)]
    with pytest.raises(ValueError, match="do not divide"):
        bounds_between("2025-01-01", "2025-12-31", 5)
    with pytest.raises(ValueError, match="before"):
        bounds_between("2025-02-01", "2025-01-31", 1)


# ---------------------------------------------------------------- structural realism
def test_simple_structural_rules_do_not_identify_misstatements(default_economy):
    econ = default_economy
    ents = econ.apparent_entries()  # what an exported general ledger shows
    lab = {e.entry_id: e.misstatement for e in econ.entries}
    sales = [e for e in ents if e.kind == "sale" and econ.in_year(e.eff_day)]
    cogs = {e.doc_id: e for e in ents if e.kind == "cogs"}
    paid: dict[str, float] = collections.defaultdict(float)
    for e in ents:
        if e.kind == "receipt":
            paid[e.description.replace("Payment for ", "")] += e.amount
    related = {p for p, v in econ.parties.items() if v.related}
    assert all(e.doc_id in cogs for e in sales)  # every sale, genuine or not, has its cost of sales
    assert all(cogs[e.doc_id].eff_day == e.eff_day for e in sales if lab[e.entry_id] != "duplicate")
    rules = {
        "cost ratio": [e for e in sales if not 0.45 <= cogs[e.doc_id].amount / e.amount <= 0.8],
        "unpaid": [e for e in sales if e.eff_day < econ.fiscal_end - 60 and paid.get(e.doc_id, 0) == 0],
        "short paid": [e for e in sales if 0 < paid.get(e.doc_id, 0) < e.amount - 0.01],
        "related party": [e for e in sales if e.counterparty in related],
    }
    for name, flagged in rules.items():
        precision = sum(1 for e in flagged if lab[e.entry_id]) / len(flagged)
        assert precision < 0.8, name


def test_fictitious_and_round_trip_sales_post_cost_of_sales(default_economy):
    m = default_economy.misstated()
    for scheme in ("fictitious", "round_trip"):
        sales = {e.doc_id for e in m if e.misstatement == scheme and e.kind == "sale"}
        costs = {e.doc_id for e in m if e.misstatement == scheme and e.kind == "cogs"}
        assert sales and sales == costs
        assert all(e.true_amount == 0 for e in m if e.misstatement == scheme)


def test_backdating_short_periods_does_not_crash_or_leave_the_year():
    e = generate(EconomyConfig(days=12, periods=4, post_days=0, sales_per_day=4), seed=1)
    m = inject(e, {"backdate": 1.0, "backdate_window": 10}, seed=2)
    bd = [x for x in m.entries if x.misstatement == "backdate"]
    assert bd and min(x.eff_day for x in m.entries) >= 0
    assert all(m.period_of(x.eff_day) < m.period_of(x.true_day) for x in bd)


# ---------------------------------------------------------------- opinions: both error types
def test_corrections_reduce_the_upper_limit(default_economy):
    on = SamplingAudit().run(default_economy, rng=4)
    off = SamplingAudit(correct=False).run(default_economy, rng=4)
    assert on.corrected_misstatement > 0
    assert on.upper_limit == pytest.approx(off.upper_limit - on.corrected_misstatement)


def test_type_one_and_type_two_errors(econ, misstated):
    clean_books = evaluate(econ, AuditResult("x", 0, tolerable_amount=1000.0, opinion="modified"))
    assert clean_books["false_modification"] == 1.0 and clean_books["audit_failure"] == 0.0
    ok = evaluate(econ, AuditResult("x", 0, tolerable_amount=1000.0, opinion="clean"))
    assert ok["false_modification"] == 0.0 and ok["audit_failure"] == 0.0
    tm = 1.0  # every misstatement is material
    fail = evaluate(misstated, AuditResult("x", 0, tolerable_amount=tm, opinion="clean"))
    assert fail["audit_failure"] == 1.0 and fail["false_modification"] == 0.0
    right = evaluate(misstated, AuditResult("x", 0, tolerable_amount=tm, opinion="modified"))
    assert right["audit_failure"] == 0.0 and right["false_modification"] == 0.0
    none = evaluate(misstated, AuditResult("x", 0))
    assert none["false_modification"] != none["false_modification"]  # NaN without an opinion


# ---------------------------------------------------------------- SAF-T export and check
def test_export_covers_the_fiscal_year(tmp_path, misstated):
    path = tmp_path / "f.xml"
    write_saft(misstated.apparent_entries(), path, misstated.start_date, period_bounds=misstated.period_bounds)
    assert saft_selection(path) == (misstated.start_date, to_date(misstated.start_date, misstated.fiscal_end))
    back = read_saft(path, misstated.start_date)
    assert len(back) == len(misstated.year_entries())
    assert all(0 <= e.eff_day <= misstated.fiscal_end for e in back)


def test_credit_notes_are_exported_with_the_customer(tmp_path):
    cn = Entry("CN1", "sale_return", 10, 12, [Line("3000", debit=500.0), Line("1500", credit=500.0)], 500.0, "C001")
    write_saft([cn], tmp_path / "cn.xml")
    text = (tmp_path / "cn.xml").read_text(encoding="utf-8")
    assert "CustomerID" in text and "SupplierID" not in text
    assert read_saft(tmp_path / "cn.xml")[0].kind == "sale_return"


def test_check_periods_and_errors(tmp_path, misstated, capsys):
    path = tmp_path / "f.xml"
    write_saft(misstated.apparent_entries(), path, misstated.start_date, period_bounds=misstated.period_bounds)
    assert main(["check", str(path), "--periods", "7"]) == 2
    assert "periods" in capsys.readouterr().err
    assert main(["check", str(path), "--start", "2025-01-01", "--end", "2025-02-28", "--periods", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["periods"] == [["2025-01-01", "2025-01-31"], ["2025-02-01", "2025-02-28"]]
    assert main(["check", str(tmp_path / "missing.xml")]) == 2


def test_cli_export_matches_the_simulator(tmp_path, capsys):
    econ = inject(generate(EconomyConfig(sales_per_day=3), seed=3), {"backdate": 0.3, "duplicate": 0.01}, seed=4)
    save_economy(econ, tmp_path / "eco")
    assert main(["saft-export", str(tmp_path / "eco"), "--out", str(tmp_path / "f.xml")]) == 0
    capsys.readouterr()
    assert main(["check", str(tmp_path / "f.xml")]) == 0
    out = json.loads(capsys.readouterr().out)
    from veriledger.audit import build_ledger

    led = build_ledger(econ)
    assert out["cutoff_flags"] == len(led.cutoff_flags())
    assert [to_date(econ.start_date, b) for _, b in econ.period_bounds] == [p[1] for p in out["periods"]]


def test_bundles_keep_the_simulated_horizon(tmp_path, misstated):
    save_economy(misstated, tmp_path / "eco")
    assert load_economy(tmp_path / "eco").last_day == misstated.last_day
