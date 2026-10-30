# VeriLedger

**Simulate blockchain-based accounting verification and measure information asymmetry.**

VeriLedger is a Python toolkit for accounting, auditing and finance research. It generates a firm's
double-entry journal together with the records its customers and suppliers hold, injects labelled
misstatements, records the journal on a hash-chained ledger with Merkle commitments, and compares how
well different verification and disclosure regimes detect misstatements, what audit opinion they
support, at what cost, and how much they narrow the information gap between insiders and outsiders.

It is a research testbed, not accounting software, and its cryptography is for simulation only.

## What it does

| Module | Purpose |
|---|---|
| `synth` | One calendar fiscal year (plus the first days of the next) of sales, purchases, cost of sales, receipts, payments and expenses for a focal firm; customers and suppliers with Zipf-distributed volumes; varying margins, late, partial and missing payments; mirror records; adoption of the shared ledger; related parties that also trade genuinely |
| `misstate` | Six labelled schemes: backdating (cut-off, also across the year end), fictitious entries, inflated amounts, duplicates, round trips with colluding related parties, and after-the-fact tampering; identifiers carry no labels |
| `ledger`, `merkle` | Append-only hash chain, per-period Merkle commitments, RFC 9162 inclusion proofs, integrity verification against independently held commitments, cut-off, duplicate and forward-dating checks |
| `audit`, `psi` | Monetary-unit and attribute sampling with a cut-off test and an audit opinion from the Stringer bound (both error types measured); interim reviews; full-population ledger checks; cross-ledger matching with adopting counterparties by private set intersection (X25519 or RFC 3526 MODP) plus residual sampling |
| `disclosure` | Reviewed interim reports with an audited annual report, a real-time public ledger (optionally pseudonymous), and an attested ledger; information-gap index, staleness, counterparty exposure |
| `lending` | Inventory-backed credit under four lender policies: over-advance and credit shortfall |
| `formats` | General-ledger CSV and SAF-T Financial XML import (credit notes, zero-amount and forward-dated entries); minimal SAF-T 1.40 export that validates against the official schema |
| `scenario`, `report`, `cli` | YAML scenarios with replications and parameter sweeps, black-and-white figures, a self-contained HTML report, and the `veriledger` command |

## Install

```bash
pip install .                    # from the accompanying source archive
pip install ".[crypto]"           # adds the X25519 simulation backend
```

Python 3.10 or later. Core dependencies: numpy, pandas, matplotlib, PyYAML.

## Quick start

Run the four bundled experiments (about a minute with two replications):

```bash
veriledger demo --replications 2 --out demo
```

Each folder contains `results.csv` (one row per replication and regime), `summary.csv` (means and
standard errors), `summary.png`/`summary.pdf` and `report.html`.

From Python:

```python
from veriledger import CrossLedgerAudit, EconomyConfig, SamplingAudit, evaluate, generate, inject

econ = generate(EconomyConfig(adoption=0.5), seed=1)
econ = inject(econ, {"backdate": 0.3, "fictitious": 0.01, "inflate": 0.01}, seed=2)
for regime in (SamplingAudit(), CrossLedgerAudit()):
    r = evaluate(econ, regime.run(econ, rng=3))
    print(r["regime"], round(r["recall_amount_annual"], 3), r["opinion"], r["cost_hours"])
```

A scenario file:

```yaml
name: Audit regimes under partial adoption
experiment: audit            # audit | disclosure | lending
seed: 2026
replications: 30
economy: {adoption: 0.5}
misstatements: {backdate: 0.3, fictitious: 0.01, inflate: 0.01, duplicate: 0.005, round_trip: 4, tamper: 0.005}
regimes: [sampling, ledger, crossledger]
sweep: {economy.adoption: [0.0, 0.25, 0.5, 0.75, 1.0]}
```

```bash
veriledger run scenario.yaml --out results
```

Other commands:

```bash
veriledger simulate --seed 1 --out economy        # CSV files, ground truth and a saved ledger
veriledger simulate --scenario scenario.yaml --replication 3 --out economy   # one replication's economy
veriledger verify economy/ledger.jsonl --commits economy/ledger.jsonl.commits.json  # local consistency only
veriledger check SAF-T_file.xml --out flags.csv   # ledger checks on a real general ledger
veriledger saft-import SAF-T_file.xml --out entries.csv
veriledger saft-export economy --out saft.xml
veriledger psi-bench --n 2000
```

## Examples

`examples/` reproduces the three experiments in the accompanying software paper:

1. `01_disclosure_regimes.py`: information gap under periodic, public and attested disclosure.
2. `02_audit_adoption.py`: detection, audit cost and audit opinions as counterparties adopt the shared
   ledger, and audit failure under collusion.
3. `03_inventory_lending.py`: inventory-backed credit with verifiable purchase records.

`benchmarks/bench.py` times ledger construction, verification and the PSI backends.

## Assumptions to check

Detection probabilities, review and audit costs and economy parameters have illustrative defaults.
They are not empirical estimates. Set them to match the setting you study; every value can be changed in the
scenario file. See `docs/concepts.md` for the model and formulas.

## Documentation

`docs/` (build with `mkdocs serve`): getting started, concepts and formulas, scenario reference, file
formats, command line, validation and limitations.

## Citation

See `CITATION.cff` for the software citation metadata.

## Authors

- Shaoni Zhou (snzhou@bjtu.edu.cn)
- Ruiji Feng (corresponding author, 25120591@bjtu.edu.cn)

## License

MIT. Optional SAF-T tests require the separately obtained schema and examples from the Norwegian Tax Administration; third-party files are not included.

## Trust, truth and privacy

- Results are generated under stylised assumptions and are not empirical estimates.
- Simulations and scoring that depend on misstatement labels require valid ground truth.
- An inclusion proof checks consistency with the supplied commitment; trustworthy use requires the commitment to be retained independently.
- All counterparty matching takes place in one process. It is a research simulation and does not establish a privacy or security guarantee. Do not use it to protect confidential data.
- Generated files without labels are not guaranteed to be blind benchmarks; hashes and mirror records can still expose anomalies.
- Saving a ledger or bundle does not certify its validity and does not provide a transaction spanning all files.

See `docs/limitations.md`, `docs/concepts.md` and `docs/formats.md` for details.
