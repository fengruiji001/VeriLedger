import json

import pandas as pd
import pytest
import yaml
from conftest import ALL_SCHEMES, SMALL

from veriledger import __version__
from veriledger.cli import BUNDLED, bundled_scenario, main
from veriledger.scenario import Scenario, run, run_once, summarize


def small(experiment, **extra):
    d = {
        "name": "t",
        "experiment": experiment,
        "seed": 1,
        "replications": 2,
        "economy": dict(SMALL),
        "misstatements": {"fictitious": 0.02, "inflate": 0.02, "backdate": 0.2},
    }
    d.update(extra)
    return Scenario.from_dict(d)


@pytest.mark.parametrize("experiment,n_regimes", [("audit", 3), ("disclosure", 3), ("lending", 4)])
def test_each_experiment_runs(experiment, n_regimes):
    df = run(small(experiment))
    assert len(df) == 2 * n_regimes
    assert {"regime", "replication"} <= set(df.columns)
    s = summarize(df)
    assert len(s) == n_regimes and (s["replications"] == 2).all()


def test_sweep_and_determinism():
    scn = small("audit", sweep={"economy.adoption": [0.0, 1.0]}, regimes=["crossledger"])
    a, b = run(scn), run(scn)
    pd.testing.assert_frame_equal(a, b)
    s = summarize(a, "economy.adoption")
    assert list(s["economy.adoption"]) == [0.0, 1.0]
    assert s.loc[1, "recall"] > s.loc[0, "recall"]


def test_regime_params_reach_regimes():
    scn = small("audit", regimes=["sampling"], regime_params={"sampling": {"cutoff_per_boundary": 0, "tolerable": 0.5}})
    row = run_once(scn, 0)[0]
    assert row["sampled"] < 20


@pytest.mark.parametrize(
    "bad",
    [
        {"experiment": "x"},
        {"regimes": ["public"]},
        {"replications": 0},
        {"sweep": {"adoption": [1]}},
        {"sweep": {"economy.adoption": [0], "economy.days": [10]}},
        {"economy": {"bogus": 1}},
        {"surprise": 1},
    ],
)
def test_validation(bad):
    d = {"experiment": "audit", **bad}
    with pytest.raises(ValueError):
        Scenario.from_dict(d)


def test_bundled_scenarios_are_valid():
    for name in BUNDLED:
        scn = Scenario.from_yaml(bundled_scenario(name))
        assert scn.sweep and scn.replications >= 20


def test_cli_run(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(yaml.safe_dump(small("disclosure").to_dict()))
    assert main(["run", str(cfg), "--out", str(tmp_path / "o"), "--replications", "1", "--quiet"]) == 0
    for f in ("results.csv", "summary.csv", "summary.png", "summary.pdf", "report.html"):
        assert (tmp_path / "o" / f).exists()
    assert "<img" in (tmp_path / "o" / "report.html").read_text()


def test_cli_simulate_verify_saft(tmp_path, capsys):
    cfg = tmp_path / "e.yaml"
    cfg.write_text(yaml.safe_dump({"economy": SMALL, "misstatements": {"tamper": 0.0, "fictitious": 0.02}}))
    out = tmp_path / "eco"
    assert main(["simulate", "--config", str(cfg), "--out", str(out), "--seed", "3"]) == 0
    assert main(["verify", str(out / "ledger.jsonl")]) == 0
    assert main(["verify", str(out / "ledger.jsonl"), "--commits", str(out / "ledger.jsonl.commits.json")]) == 0
    # tamper with the stored record: verification fails
    lines = (out / "ledger.jsonl").read_text().splitlines()
    d = json.loads(lines[3])
    d["record"]["amount"] += 1
    lines[3] = json.dumps(d)
    (out / "ledger.jsonl").write_text("\n".join(lines) + "\n")
    assert main(["verify", str(out / "ledger.jsonl")]) == 1
    assert main(["saft-export", str(out), "--out", str(tmp_path / "f.xml")]) == 0
    assert main(["saft-import", str(tmp_path / "f.xml"), "--out", str(tmp_path / "g.csv")]) == 0
    assert (tmp_path / "g.csv").read_text().startswith("entry_id")
    capsys.readouterr()


def test_cli_psi_bench_and_version(capsys):
    assert main(["psi-bench", "--n", "40", "--backend", "plain"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["matched"] == 20
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_cli_demo(tmp_path):
    assert main(["demo", "--out", str(tmp_path), "--replications", "1", "--quiet"]) == 0
    for name in BUNDLED:
        assert (tmp_path / name / "summary.csv").exists()


@pytest.mark.parametrize(
    "bad,match",
    [
        ({"experiment": "lending", "options": {"advance_verifed": 0.9}}, "unknown options"),
        ({"experiment": "audit", "regime_params": {"crosledger": {}}}, "unknown regimes"),
        ({"experiment": "audit", "regime_params": {"crossledger": {"psi_backnd": "x"}}}, "psi_backnd"),
        ({"experiment": "disclosure", "regime_params": {"public": {"lag": 3}}}, "unknown parameters"),
        ({"experiment": "disclosure", "regime_params": {"attested": {"audit": {"psi": 1}}}}, "psi"),
        ({"experiment": "lending", "regime_params": {"field_exam": {"exam": {"tolerable": 2}}}}, "tolerable"),
        ({"economy": {"adoption": 1.5}}, "adoption"),
        ({"misstatements": {"inflate": -0.1}}, "inflate"),
        ({"detection": {"vouch": {"fictitous": 0.5}}}, "fictitous"),
        ({"costs": {"per_item": -1}}, "negative"),
        ({"sweep": {"economy.adoption": []}}, "non-empty"),
    ],
)
def test_scenario_rejects_mistakes_before_running(bad, match):
    with pytest.raises(ValueError, match=match):
        Scenario.from_dict({"replications": 1, **bad})


def test_regime_streams_do_not_depend_on_the_list():
    base = {"experiment": "audit", "replications": 2, "economy": SMALL, "misstatements": ALL_SCHEMES}
    both = run(Scenario.from_dict({**base, "regimes": ["sampling", "crossledger"]}))
    one = run(Scenario.from_dict({**base, "regimes": ["crossledger"]}))
    a = both[both.regime == "crossledger"].reset_index(drop=True)
    b = one.reset_index(drop=True)
    assert a["recall_amount"].tolist() == b["recall_amount"].tolist()
    assert a["cost_hours"].tolist() == b["cost_hours"].tolist()


def test_lending_regime_params_are_used():
    base = {
        "experiment": "lending",
        "replications": 1,
        "economy": SMALL,
        "regimes": ["field_exam"],
        "misstatements": {"fictitious": 0.03, "target": "purchase"},
    }
    cheap = run(Scenario.from_dict({**base, "regime_params": {"field_exam": {"exam": {"tolerable": 0.2}}}}))
    dear = run(Scenario.from_dict(base))
    assert cheap["cost_hours"].iloc[0] < dear["cost_hours"].iloc[0]


def test_cli_rejects_zero_replications(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text("experiment: audit\n")
    with pytest.raises(ValueError):
        main(["run", str(cfg), "--out", str(tmp_path / "o"), "--replications", "0", "--quiet"])


def test_simulate_exports_a_scenario_replication(tmp_path):
    from veriledger.formats import load_economy
    from veriledger.scenario import economy_for

    cfg = tmp_path / "s.yaml"
    cfg.write_text(yaml.safe_dump({**small("audit").to_dict(), "sweep": {"economy.adoption": [0.0, 1.0]}}))
    assert (
        main(
            [
                "simulate",
                "--scenario",
                str(cfg),
                "--replication",
                "1",
                "--sweep-value",
                "1.0",
                "--out",
                str(tmp_path / "e"),
            ]
        )
        == 0
    )
    back = load_economy(tmp_path / "e")
    scn = Scenario.from_yaml(cfg).with_value("economy.adoption", 1.0)
    ref = economy_for(scn, 1)
    assert [e.record() for e in back.entries] == [e.record() for e in ref.entries]
    assert all(p.adopts for p in back.parties.values())
