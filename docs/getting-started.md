# Getting started

## Installation

```bash
pip install .                 # from a clone of the repository
pip install ".[crypto]"       # X25519 simulation backend; not a production privacy protocol
pip install ".[dev]"          # tests and linting
```

## First run

```bash
veriledger demo --replications 2 --out demo
```

This runs the four bundled scenarios (`audit_adoption`, `audit_collusion`, `disclosure_regimes`,
`lending_inventory`) and writes one folder per scenario:

| File | Content |
|---|---|
| `results.csv` | one row per replication, regime and sweep value |
| `summary.csv` | means and standard errors by regime and sweep value |
| `summary.png`, `summary.pdf` | two-panel figure of the main metrics |
| `report.html` | self-contained report with the figure, summary table and scenario |

## A single economy in Python

```python
from veriledger import EconomyConfig, generate, inject, build_ledger, LedgerAudit, evaluate

econ = generate(EconomyConfig(adoption=0.5), seed=1)
econ = inject(econ, {"backdate": 0.3, "tamper": 0.01}, seed=2)
ledger = build_ledger(econ)          # original records, then database tampering applied
print(ledger.verify().ok)            # False: tampered records no longer match the replicas
print(evaluate(econ, LedgerAudit().run(econ, ledger=ledger, rng=0))["recall"])
```

## Your own data

```bash
veriledger check SAF-T_file.xml --out flags.csv      # or a general-ledger CSV with --start
```

records the general ledger on a ledger, with quarterly periods of the fiscal year in the SAF-T header
(or `--start YYYY-MM-DD --periods N`), and lists entries posted after the end of the period they
claim, repeated documents of a counterparty and entries entered before their transaction date.
`veriledger saft-import file.xml --out entries.csv` converts SAF-T Financial entries to CSV, and
`formats.read_gl_csv(path, mapping={...})` reads any posting-line CSV. Cross-ledger matching needs the
counterparties' records, which real data rarely provide; the synthetic economy supplies them.

## Reproducing one replication

`veriledger simulate --scenario s.yaml --replication 3 --sweep-value 0.5 --out eco` writes the economy
that replication 3 of `s.yaml` uses at sweep value 0.5, with its ledger and ground truth.
