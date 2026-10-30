"""Tests for audit targets, replication groups and ledger persistence."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
import yaml

from veriledger.cli import main
from veriledger.ledger import Ledger
from veriledger.model import make_entry
from veriledger.report import _plt, plot_summary
from veriledger.scenario import Scenario, economy_for, run, run_once, summarize

SMALL = {
    "days": 10,
    "periods": 2,
    "post_days": 0,
    "n_customers": 4,
    "n_suppliers": 3,
    "related_parties": 0,
    "sales_per_day": 1,
    "purchases_per_day": 1,
    "expenses_per_day": 0,
}


@pytest.mark.parametrize("regime", ["sampling", "ledger", "crossledger"])
def test_audit_scenario_uses_the_configured_purchase_population(regime):
    scn = Scenario.from_dict(
        {
            "replications": 1,
            "economy": SMALL,
            "regimes": [regime],
            "misstatements": {"target": "purchase", "fictitious": 0.5},
        }
    )
    purchases = economy_for(scn, 0).year_entries("purchase")
    row = run_once(scn, 0)[0]
    assert row["population"] == len(purchases)
    assert row["n_misstated"] == sum(bool(e.misstatement) for e in purchases) > 0
    assert row["amount_misstated"] > 0


@pytest.mark.parametrize("values", [[0.5, 0.5], [1, 1.0]])
def test_duplicate_sweep_values_cannot_reuse_the_same_replications(values):
    with pytest.raises(ValueError, match="duplicate"):
        Scenario.from_dict({"economy": SMALL, "sweep": {"economy.adoption": values}})


@pytest.mark.parametrize("value", [{"bad": 1}, [0.5], float("nan"), float("inf")])
def test_sweep_values_must_be_finite_groupable_scalars(value):
    with pytest.raises(ValueError):
        Scenario.from_dict({"economy": SMALL, "sweep": {"economy.adoption": [value]}})


def raw_results():
    return pd.DataFrame(
        {
            "economy.days": [None, None, 10, 10],
            "regime": ["sampling"] * 4,
            "replication": [0, 1, 0, 1],
            "recall_amount_annual": [0.2, 0.4, 0.6, 0.8],
            "cost_hours": [10.0, 12.0, 20.0, 22.0],
        }
    )


def test_summary_retains_the_nullable_default_parameter_group():
    summary = summarize(raw_results(), "economy.days")
    assert len(summary) == 2
    default = summary[summary["economy.days"].isna()].iloc[0]
    assert default.replications == 2
    assert default.recall_amount_annual == pytest.approx(0.3)
    assert default.recall_amount_annual_se == pytest.approx(0.1)


def test_plot_includes_default_parameters_as_visible_categories():
    summary = pd.DataFrame(
        {
            "economy.days": [None, 10],
            "regime": ["sampling"] * 2,
            "recall_amount_annual": [0.3, 0.7],
            "recall_amount_annual_se": [0.1, 0.1],
            "cost_hours": [11.0, 21.0],
            "cost_hours_se": [1.0, 1.0],
        }
    )
    fig = plot_summary(summary, "audit", "economy.days")
    try:
        assert "default" in [t.get_text().lower() for t in fig.axes[0].get_xticklabels()]
        assert np.isfinite(np.asarray(fig.axes[0].lines[0].get_xdata(), dtype=float)).all()
        assert len(fig.axes[0].lines[0].get_xdata()) == 2
    finally:
        _plt().close(fig)


def test_plot_uses_a_visible_metric_for_its_legend():
    summary = pd.DataFrame({"economy.adoption": [0.0, 1.0], "regime": ["sampling"] * 2, "cost_hours": [10.0, 12.0]})
    fig = plot_summary(summary, "audit", "economy.adoption")
    try:
        fig.canvas.draw()
        assert not fig.axes[0].get_visible() and fig.axes[1].get_visible()
        assert [text.get_text() for text in fig.legends[0].get_texts()] == ["Sampling audit"]
    finally:
        _plt().close(fig)


def test_cli_reports_an_error_free_scenario_with_nullable_sweep(tmp_path):
    cfg = tmp_path / "default.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "economy": {**SMALL, "sales_per_day": 0.1, "purchases_per_day": 0.1},
                "replications": 1,
                "regimes": ["sampling"],
                "sweep": {"economy.days": [None, 10]},
            }
        )
    )
    out = tmp_path / "report"
    assert main(["run", str(cfg), "--out", str(out), "--quiet"]) == 0
    summary = pd.read_csv(out / "summary.csv")
    assert len(summary) == 2 and summary["economy.days"].isna().sum() == 1
    assert all((out / name).stat().st_size > 0 for name in ("results.csv", "summary.png", "report.html"))


@pytest.mark.parametrize("default", [False, True])
def test_summary_rejects_repeated_replication_ids_within_a_group(default):
    df = raw_results()
    df = df.iloc[:2] if default else df.iloc[2:]
    with pytest.raises(ValueError, match="replication"):
        summarize(pd.concat([df, df]), "economy.days")


@pytest.mark.parametrize("runner", [run, run_once])
def test_run_entry_points_validate_direct_scenario_objects(runner):
    scn = Scenario(economy=SMALL, regimes=["sampling"], replications=0)
    with pytest.raises(ValueError, match="replications"):
        runner(scn, 0) if runner is run_once else runner(scn)


def test_direct_scenario_uses_default_regimes():
    result = run(Scenario(economy=SMALL, replications=1))
    assert len(result) == 3
    assert set(result.regime) == {"sampling", "ledger", "crossledger"}


@pytest.mark.parametrize("kind", ["csv", "xml"])
def test_check_reports_missing_dates_without_a_traceback(kind, tmp_path, capsys):
    path = tmp_path / f"bad.{kind}"
    if kind == "csv":
        path.write_text("entry_id,other,date,account,debit,credit\nA,x\n")
    else:
        path.write_text(
            "<AuditFile><GeneralLedgerEntries><Transaction><TransactionDate/></Transaction></GeneralLedgerEntries></AuditFile>"
        )
    assert main(["check", str(path)]) == 2
    assert "error:" in capsys.readouterr().err


def ledger():
    return Ledger.from_entries(
        "F", [make_entry("A", "sale", 1, 100), make_entry("B", "sale", 2, 200)], [(0, 4), (5, 9)]
    )


@pytest.mark.parametrize("bad", [None, [], "bad", 42])
def test_integrity_verifier_reports_non_mapping_record_damage(bad):
    led = ledger()
    led.records[0] = bad
    report = led.verify()
    assert not report.ok and report.modified == [0]
    assert any("invalid record at index 0" in e for e in report.errors)
    assert led.records[0] is bad


@pytest.mark.parametrize("entry_id", ["A", "B"])
@pytest.mark.parametrize("bad", ["set", "bytes", "complex", "circular"])
def test_serialization_failure_preserves_both_saved_ledger_files(entry_id, bad, tmp_path):
    led = ledger()
    path = tmp_path / "ledger.jsonl"
    led.save(path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    value = {"set": {1}, "bytes": b"bad", "complex": 1j, "circular": []}[bad]
    if bad == "circular":
        value.append(value)
    led.tamper(entry_id, description=value)
    with pytest.raises((TypeError, ValueError, RecursionError)):
        led.save(path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_invalid_commitment_serialization_preserves_saved_ledger(tmp_path):
    led = ledger()
    path = tmp_path / "ledger.jsonl"
    led.save(path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    led.tamper("A", description="new data")
    led.commits[0] = replace(led.commits[0], root="invalid")
    with pytest.raises((TypeError, AttributeError, ValueError)):
        led.save(path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_blocked_commitment_sidecar_does_not_replace_the_ledger(tmp_path):
    led = ledger()
    path = tmp_path / "ledger.jsonl"
    led.save(path)
    original = path.read_bytes()
    sidecar = tmp_path / "ledger.jsonl.commits.json"
    sidecar.unlink()
    sidecar.mkdir()
    led.tamper("A", description="new data")
    with pytest.raises((OSError, ValueError)):
        led.save(path)
    assert path.read_bytes() == original


def test_missing_replica_hash_does_not_truncate_saved_ledger(tmp_path):
    led = ledger()
    path = tmp_path / "ledger.jsonl"
    led.save(path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    led.leaves.pop()
    with pytest.raises((IndexError, ValueError)):
        led.save(path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
