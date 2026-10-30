# Validation

The test suite (`python -m pytest`) checks:

* every Merkle inclusion proof for trees of 1 to 33 leaves; rejection of altered leaves and paths, of
  proofs moved to another index, and of internal nodes presented as leaves;
* that any single change to a stored record is found by `verify`, that a rewritten ledger file passes
  its own checks but fails against independently held commitments, and that deleted records are
  reported;
* sample-size formulas against closed forms, selection frequencies of systematic MUS against
  `min(1, a n / BV)`, Poisson reliability factors against the published table, and the Stringer bound
  against hand-computed values;
* the detection frequency of one misstated entry under sampling against its analytic probability;
* that clean books receive a clean opinion and heavily misstated books a modified one;
* that the three PSI backends return the same intersection as plain set operations;
* that cross-ledger matching with full adoption detects every scheme except colluding round trips;
* that the public ledger is scored on the records as posted, that cleared flags count as verified
  collateral, that identifiers do not reveal labels, and that simple structural rules (missing or
  unusual cost of sales, unpaid or short-paid invoices, related-party sales) do not identify
  misstatements with high precision;
* both opinion error types (audit failure and false modification), and that corrections reduce the
  upper misstatement limit by their amount;
* that the default fiscal year follows the calendar and that a SAF-T export and `veriledger check`
  see the same fiscal year, periods and cut-off flags as the simulator;
* that fiscal-year transactions do not depend on how many days of the following year are simulated;
* that unknown or out-of-range scenario parameters are rejected when a file is loaded;
* CSV, SAF-T and bundle round trips, credit notes, zero-amount and forward-dated entries, repeated
  transaction ids, and (optionally, with `VERILEDGER_SAFT_DIR`) that exported SAF-T files validate
  against the official 1.40 schema and that the official example files import and pass `check`.

CI also runs ruff, mypy and a minimum coverage of 90%. `benchmarks/bench.py` reports run times for
ledgers of 10,000 to 1,000,000 records.

## Running the tests

Install development dependencies with `python -m pip install ".[dev]"`, then run:

```bash
python -m pytest
```

To run the optional SAF-T schema and example tests, obtain the Norwegian Tax Administration's
[SAF-T repository](https://github.com/Skatteetaten/saf-t) separately and set `VERILEDGER_SAFT_DIR`
to its root. The official files are not distributed with this package.

An optional independent Poisson numerical check is available when SciPy is installed:

```bash
python tools/check_statistics.py
```

Test and benchmark outcomes depend on the environment. Benchmark timings are not portable performance guarantees.

