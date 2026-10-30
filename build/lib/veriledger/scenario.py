"""Scenario files: configure an experiment in YAML, run replications and parameter sweeps.

Example::

    name: audit-adoption
    experiment: audit            # audit | disclosure | lending
    seed: 7
    replications: 20
    economy: {adoption: 0.5, n_customers: 40}
    misstatements: {backdate: 0.3, fictitious: 0.01, inflate: 0.01}
    regimes: [sampling, ledger, crossledger]
    regime_params: {crossledger: {psi_backend: plain}}
    sweep: {economy.adoption: [0.0, 0.25, 0.5, 0.75, 1.0]}

Every replication draws one economy and one set of misstatements; all regimes are applied to the same
draw (common random numbers). Sweeps change one parameter at a time, given as ``section.field``.
"""

from __future__ import annotations

import copy
import math
import zlib
from dataclasses import dataclass, field
from numbers import Real
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .audit import CostModel, DetectionModel, make_regime
from .disclosure import REGIME_NAMES as DISCLOSURE_REGIMES
from .disclosure import run_regime
from .lending import POLICIES, lend
from .metrics import evaluate
from .misstate import MisstatementSpec, inject
from .synth import EconomyConfig, generate
from .validation import finite, integer

EXPERIMENTS = {
    "audit": ("sampling", "ledger", "crossledger"),
    "disclosure": DISCLOSURE_REGIMES,
    "lending": POLICIES,
}
SECTIONS = ("economy", "misstatements", "detection", "costs", "options")
#: keys allowed in ``options`` for each experiment
OPTIONS = {
    "audit": (),
    "disclosure": ("report_lag",),
    "lending": ("advance_verified", "advance_unverified", "opening_days"),
}
#: keys allowed in ``regime_params`` for disclosure regimes and lending policies (audit regimes take
#: the fields of their class); ``audit``, ``exam`` and ``crossledger`` hold the parameters of the
#: verification procedure the regime uses
REGIME_KEYS = {
    "periodic": ("audit", "report_lag", "annual_lag"),
    "public": ("report_lag", "close_lag", "pseudonymize"),
    "attested": ("audit", "report_lag", "attest_lag"),
    "trust": (),
    "haircut": (),
    "field_exam": ("exam",),
    "ledger": ("crossledger",),
}


def regime_kwargs(experiment: str, name: str, params: dict | None) -> dict:
    """Turn ``regime_params[name]`` into keyword arguments, rejecting unknown keys."""
    params = dict(params or {})
    if experiment == "audit":
        return {"regime": make_regime(name, params)}
    allowed = REGIME_KEYS[name]
    unknown = set(params) - set(allowed)
    if unknown:
        raise ValueError(f"unknown parameters for {name!r}: {sorted(unknown)}; allowed: {list(allowed)}")
    nested = {
        "audit": "sampling" if name == "periodic" else "crossledger",
        "exam": "sampling",
        "crossledger": "crossledger",
    }
    defaults: dict[tuple[str, str], dict] = {
        ("attested", "audit"): {"residual_sampling": False},
        ("field_exam", "exam"): {"cutoff_per_boundary": 0},
        ("ledger", "crossledger"): {"residual_sampling": False},
    }
    for key in ("report_lag", "annual_lag", "attest_lag", "close_lag"):
        if key in params and params[key] is not None:
            integer(key, params[key])
    if "pseudonymize" in params and not isinstance(params["pseudonymize"], bool):
        raise ValueError("pseudonymize must be a boolean")
    for key in ("audit", "exam", "crossledger"):
        if key in allowed and (key in params or (name, key) in defaults):
            params[key] = make_regime(nested[key], {**defaults.get((name, key), {}), **(params.get(key) or {})})
    if name == "attested" and params["audit"].residual_sampling:
        raise ValueError("daily attestation requires residual_sampling=False")
    return params


def regime_seed(seed: int, rep: int, name: str) -> np.random.SeedSequence:
    """Random stream of one regime in one replication; it depends on the regime's name, not on its
    position in the list, so adding or removing a regime leaves the others unchanged."""
    return np.random.SeedSequence([seed, rep, zlib.crc32(name.encode("utf-8"))])


@dataclass
class Scenario:
    name: str = "scenario"
    experiment: str = "audit"
    seed: int = 1
    replications: int = 20
    economy: dict = field(default_factory=dict)
    misstatements: dict = field(default_factory=dict)
    regimes: list[str] = field(default_factory=list)
    regime_params: dict = field(default_factory=dict)
    detection: dict = field(default_factory=dict)
    costs: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    sweep: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> Scenario:
        d = dict(d)
        unknown = set(d) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown scenario keys: {sorted(unknown)}")
        scn = cls(**d)
        scn.validate()
        return scn

    @classmethod
    def from_yaml(cls, path: str | Path) -> Scenario:
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

    def to_dict(self) -> dict:
        return {k: copy.deepcopy(getattr(self, k)) for k in self.__dataclass_fields__}

    def validate(self) -> None:
        integer("seed", self.seed)
        integer("replications", self.replications, 1)
        for key in ("economy", "misstatements", "regime_params", "detection", "costs", "options", "sweep"):
            if not isinstance(getattr(self, key), dict):
                raise ValueError(f"{key} must be a mapping")
        if not isinstance(self.regimes, list) or any(not isinstance(r, str) for r in self.regimes):
            raise ValueError("regimes must be a list of names")
        if len(set(self.regimes)) != len(self.regimes):
            raise ValueError("regimes must not contain duplicates")
        if "report_lag" in self.options:
            integer("report_lag", self.options["report_lag"])
        if "opening_days" in self.options:
            integer("opening_days", self.options["opening_days"])
        for key in ("advance_verified", "advance_unverified"):
            if key in self.options:
                finite(key, self.options[key], 0.0, 1.0)
        if self.options.get("advance_unverified", 0.5) > self.options.get("advance_verified", 0.8):
            raise ValueError("advance_unverified must not exceed advance_verified")
        if self.experiment not in EXPERIMENTS:
            raise ValueError(f"experiment must be one of {sorted(EXPERIMENTS)}")
        if not self.regimes:
            self.regimes = list(EXPERIMENTS[self.experiment])
        bad = [r for r in self.regimes if r not in EXPERIMENTS[self.experiment]]
        if bad:
            raise ValueError(f"regimes {bad} are not valid for experiment {self.experiment!r}")
        if self.replications < 1:
            raise ValueError("replications must be at least 1")
        if len(self.sweep) > 1:
            raise ValueError("sweep one parameter at a time")
        for key, values in self.sweep.items():
            sec = key.split(".", 1)[0]
            if "." not in key or sec not in SECTIONS + ("regime_params",):
                raise ValueError(f"sweep key {key!r} must look like 'economy.adoption'")
            if not isinstance(values, list) or not values:
                raise ValueError(f"sweep {key!r} needs a non-empty list of values")
            seen = []
            for value in values:
                if value is not None and not isinstance(value, (str, Real)):
                    raise ValueError(f"sweep {key!r} values must be scalar parameters")
                if isinstance(value, Real) and not math.isfinite(value):
                    raise ValueError(f"sweep {key!r} values must be finite")
                if value in seen:
                    raise ValueError(f"sweep {key!r} contains duplicate parameter values")
                seen.append(value)
        bad_opts = set(self.options) - set(OPTIONS[self.experiment])
        if bad_opts:
            raise ValueError(
                f"unknown options for experiment {self.experiment!r}: {sorted(bad_opts)}; "
                f"allowed: {list(OPTIONS[self.experiment])}"
            )
        bad_regimes = set(self.regime_params) - set(EXPERIMENTS[self.experiment])
        if bad_regimes:
            raise ValueError(f"regime_params for unknown regimes: {sorted(bad_regimes)}")
        for name, params in self.regime_params.items():
            regime_kwargs(self.experiment, name, params)
        EconomyConfig.from_dict(self.economy).validate()
        MisstatementSpec.from_dict(self.misstatements)
        DetectionModel.from_dict(self.detection)
        CostModel.from_dict(self.costs)
        # sweeping a value validates each variant when it is built (Scenario.with_value)

    def with_value(self, key: str, value: Any) -> Scenario:
        d = self.to_dict()
        parts = key.split(".")
        node = d
        for p in parts[:-1]:
            if node.get(p) is None:
                node[p] = {}
            if not isinstance(node[p], dict):
                raise ValueError(f"sweep path component {p!r} must be a mapping")
            node = node[p]
        node[parts[-1]] = value
        d["sweep"] = {}
        return Scenario.from_dict(d)


def _clean(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def economy_for(scn: Scenario, rep: int):
    """The economy (with misstatements) that replication ``rep`` of ``scn`` uses."""
    s_econ, s_mis = np.random.SeedSequence([scn.seed, rep]).spawn(2)
    econ = generate(EconomyConfig.from_dict(scn.economy), np.random.default_rng(s_econ))
    spec = MisstatementSpec.from_dict(scn.misstatements)
    if scn.experiment == "lending" and "target" not in scn.misstatements:
        spec.target = "purchase"
    return inject(econ, spec, np.random.default_rng(s_mis))


def run_once(scn: Scenario, rep: int) -> list[dict]:
    """Run all regimes of ``scn`` on replication ``rep``."""
    scn.validate()
    econ = economy_for(scn, rep)
    det, cm = DetectionModel.from_dict(scn.detection), CostModel.from_dict(scn.costs)
    rows = []
    for name in scn.regimes:
        rng = np.random.default_rng(regime_seed(scn.seed, rep, name))
        kw = regime_kwargs(scn.experiment, name, scn.regime_params.get(name))
        if scn.experiment == "audit":
            kind = scn.misstatements.get("target", "sale")
            res = kw["regime"].run(econ, kind, rng, det, cm)
            row = evaluate(econ, res, kind)
        elif scn.experiment == "disclosure":
            if name in ("periodic", "attested"):
                kw.update(detection=det, costs=cm)
            kw.setdefault("report_lag", scn.options.get("report_lag", 45))
            row = run_regime(name, econ, rng=rng, **kw).summary()
        else:
            kw.update({k: v for k, v in scn.options.items() if k in OPTIONS["lending"]})
            row = lend(econ, name, rng=rng, detection=det, costs=cm, **kw).summary()
            row["regime"] = row.pop("policy")
        row["replication"] = rep
        rows.append({k: _clean(v) for k, v in row.items()})
    return rows


def run(scn: Scenario, progress: bool = False) -> pd.DataFrame:
    """Run the scenario (and its sweep) and return one row per replication and regime."""
    scn.validate()
    if scn.sweep:
        ((key, values),) = scn.sweep.items()
        variants = [(key, v, scn.with_value(key, v)) for v in values]
    else:
        variants = [(None, None, scn)]
    rows = []
    for key, value, s in variants:
        for rep in range(s.replications):
            for row in run_once(s, rep):
                if key:
                    row[key] = value
                rows.append(row)
        if progress:
            print(f"  {key}={value}: done" if key else "  done")
    df = pd.DataFrame(rows)
    front = [c for c in (next(iter(scn.sweep), None), "regime", "replication") if c and c in df]
    return df[front + [c for c in df.columns if c not in front]]


def summarize(df: pd.DataFrame, sweep_key: str | None = None) -> pd.DataFrame:
    """Mean and standard error of numeric metrics by (sweep value, regime)."""
    keys = [k for k in (sweep_key, "regime") if k]
    if "replication" in df and df.duplicated(keys + ["replication"]).any():
        raise ValueError("duplicate replication identifiers within a result group")
    num = [c for c in df.columns if c not in keys + ["replication"] and pd.api.types.is_numeric_dtype(df[c])]
    g = df.groupby(keys, sort=False, dropna=False)[num]
    mean = g.mean()
    se = g.std(ddof=1) / np.sqrt(g.count())
    out = mean.join(se.add_suffix("_se"))
    out.insert(0, "replications", g.size())
    return out.reset_index()
