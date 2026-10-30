# VeriLedger User Manual

**Version:** 0.3.10  
**Software:** VeriLedger  
**Repository:** https://github.com/fengruiji001/VeriLedger  
**Support:** 25120591@bjtu.edu.cn | https://github.com/fengruiji001/VeriLedger/issues

VeriLedger is a Python research toolkit for simulating accounting verification, disclosure, and inventory-backed lending. This manual combines the documentation shipped with version 0.3.10 into one file. It is intended for researchers and other users running the software locally.

**Scope:** The software uses simulated data and illustrative assumptions. It does not operate a deployed blockchain, establish the economic truth of transactions, or provide a verified privacy guarantee. Do not use the simulated matching methods to protect confidential data.

## Contents

1. [Getting started](#getting-started)
2. [Command line](#command-line)
3. [Scenario files](#scenario-files)
4. [File formats](#file-formats)
5. [Python API](#python-api)
6. [Concepts and formulas](#concepts-and-formulas)
7. [Validation](#validation)
8. [Limitations](#limitations)

---

## Getting started

### Installation

```bash
pip install .                 # from a clone of the repository
pip install ".[crypto]"       # X25519 simulation backend; not a production privacy protocol
pip install ".[dev]"          # tests and linting
```

### First run

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

### A single economy in Python

```python
from veriledger import EconomyConfig, generate, inject, build_ledger, LedgerAudit, evaluate

econ = generate(EconomyConfig(adoption=0.5), seed=1)
econ = inject(econ, {"backdate": 0.3, "tamper": 0.01}, seed=2)
ledger = build_ledger(econ)          # original records, then database tampering applied
print(ledger.verify().ok)            # False: tampered records no longer match the replicas
print(evaluate(econ, LedgerAudit().run(econ, ledger=ledger, rng=0))["recall"])
```

### Your own data

```bash
veriledger check SAF-T_file.xml --out flags.csv      # or a general-ledger CSV with --start
```

records the general ledger on a ledger, with quarterly periods of the fiscal year in the SAF-T header
(or `--start YYYY-MM-DD --periods N`), and lists entries posted after the end of the period they
claim, repeated documents of a counterparty and entries entered before their transaction date.
`veriledger saft-import file.xml --out entries.csv` converts SAF-T Financial entries to CSV, and
`formats.read_gl_csv(path, mapping={...})` reads any posting-line CSV. Cross-ledger matching needs the
counterparties' records, which real data rarely provide; the synthetic economy supplies them.

### Reproducing one replication

`veriledger simulate --scenario s.yaml --replication 3 --sweep-value 0.5 --out eco` writes the economy
that replication 3 of `s.yaml` uses at sweep value 0.5, with its ledger and ground truth.

---

## Command line

| Command | Purpose |
|---|---|
| `veriledger run SCENARIO.yaml --out DIR [--replications N]` | run a scenario |
| `veriledger demo --out DIR [--replications N]` | run the four bundled scenarios |
| `veriledger simulate [--config FILE] [--seed S] --out DIR [--no-truth]` | write one economy and its ledger |
| `veriledger simulate --scenario FILE --replication R [--sweep-value V] --out DIR` | write the economy of one replication of a scenario |
| `veriledger check FILE [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--periods 4] [--out flags.csv] [--ledger L.jsonl]` | record a SAF-T or general-ledger CSV file on a ledger and list cut-off, duplicate and forward-dated entries; the fiscal year comes from `--start`/`--end`, the SAF-T header, or twelve months from the first transaction; exit code 2 if a file cannot be read or the periods do not fit |
| `veriledger verify LEDGER.jsonl [--commits FILE]` | integrity check against commitments; exit code 1 if it fails, 2 if a file cannot be read |
| `veriledger saft-import FILE.xml --out entries.csv [--start YYYY-MM-DD]` | SAF-T to CSV |
| `veriledger saft-export DIR --out file.xml` | economy bundle (the firm's database as it stands) to a minimal SAF-T 1.40 file of its fiscal year |
| `veriledger psi-bench [--n N] [--backend all]` | time the PSI backends |

`verify` without `--commits` compares the ledger only with the commitments stored next to it, which
whoever controls the file can rewrite, and says so. With `--commits`, it reports that an external file
was supplied but does not assert that its custody or provenance is independent. A complete check of
late status requires the commitment for every period containing records.

---

## Scenario files

| Key | Meaning |
|---|---|
| `name` | title used in reports |
| `experiment` | `audit`, `disclosure` or `lending` |
| `seed`, `replications` | replication `r` draws its economy from `SeedSequence([seed, r])`, shared by all regimes; each regime's own stream is derived from `(seed, r, regime name)` |
| `economy` | fields of `EconomyConfig` |
| `misstatements` | fields of `MisstatementSpec` (`target` defaults to `purchase` for lending) |
| `regimes` | audit: `sampling`, `ledger`, `crossledger`; disclosure: `periodic`, `public`, `attested`; lending: `trust`, `haircut`, `field_exam`, `ledger` |
| `regime_params` | per-regime parameters. Audit regimes take the fields of their class, e.g. `crossledger: {psi_backend: x25519, residual: {confidence: 0.9}}`. Disclosure: `periodic: {audit, report_lag, annual_lag}`, `public: {report_lag, close_lag, pseudonymize}`, `attested: {audit, report_lag, attest_lag}`. Lending: `field_exam: {exam}`, `ledger: {crossledger}` |
| `detection` | `vouch`, `investigate` and `review` probabilities by scheme |
| `costs` | fields of `CostModel` (`sampling_fixed`, `per_item`, `ledger_setup`, `per_entry_auto`, `per_investigation`, `psi_setup`, `per_psi_element`, `review_per_period`) |
| `options` | `report_lag` (disclosure); `advance_verified`, `advance_unverified`, `opening_days` (lending) |
| `sweep` | one parameter, e.g. `economy.adoption: [0, 0.5, 1]` |

`EconomyConfig` fields: `firm`, `start_date`, `days` (default `null`: the calendar year from
`start_date`), `periods`, `post_days`, `n_customers`, `n_suppliers`, `sales_per_day`,
`purchases_per_day`, `expenses_per_day`, `sale_mean`, `sale_sigma`, `purchase_mean`, `purchase_sigma`,
`expense_mean`, `cost_ratio`, `cost_sigma`, `customer_zipf`, `supplier_zipf`, `seasonality`,
`record_lag_probs`, `payment_terms`, `late_payment`, `unpaid`, `short_payment`, `adoption`,
`adoption_by_size`, `related_parties`, `related_purchases`, `mirror_noise`.

`SamplingAudit` fields (for `sampling`, the `residual` sample of `crossledger`, the `audit` of
`periodic` and the `exam` of `field_exam`): `method` (`mus`, `attribute`, `random`), `confidence`,
`tolerable`, `expected`, `expansion`, `correct`, `cutoff_window`, `cutoff_per_boundary`,
`duplicate_test`.
Ledger and cross-ledger regimes also accept `correct`; cross-ledger global `correct=False` prevents all corrections.

`MisstatementSpec` fields: `backdate`, `backdate_window`, `fictitious`, `inflate`, `inflate_factor`,
`duplicate`, `round_trip`, `round_trip_mean`, `tamper`, `tamper_factor`, `target`, `end_of_period_days`.

Unknown keys, unknown regimes in `regime_params` and out-of-range values raise an error when the file
is loaded, so typos do not pass silently or surface only after a long run.

Counts must be integers, numeric parameters must be finite, delays must be nonnegative, rates must
be in [0,1], and `advance_unverified <= advance_verified`. Regime names must be unique.
`tamper_factor` is positive because the injected schemes model overstatements.

### Audit populations and replication groups

For `experiment: audit`, `misstatements.target` selects the population for every audit procedure and
its scoring: `sale` by default, or `purchase`. Backdating and round-trip injection retain their own
scheme definitions. Disclosure measures revenue and lending measures purchase-backed inventory.

Sweep values must be scalar parameters (finite numbers, strings, booleans or `null` where the target
parameter permits it), with no duplicate or numerically equivalent values. Replication streams use
the same IDs at different parameter values, but repeating one parameter value repeats the same draws.
`summarize` rejects duplicate replication IDs within a parameter/regime group instead of counting
them as independent draws. Nullable defaults, such as `economy.days: null`, remain a group and appear
as `default` on categorical plot axes. Direct `Scenario(...)` objects are validated by `run` and
`run_once`; valid empty regime lists receive the experiment's default regimes.

When an error-free scenario has no recall metric, its report still plots available costs and obtains
the legend from a visible metric panel. An absent metric is not represented as a measured zero.

### Nested null defaults

An explicitly null nested default, such as `regime_params.crossledger.residual: null`, may be expanded
when sweeping `regime_params.crossledger.residual.confidence`. The variant receives a copied mapping
and normal default validation; the original Scenario remains unchanged. A path through a scalar
field raises ValueError rather than a dictionary assignment TypeError.

---

## File formats

### General-ledger CSV

One row per posting line with columns `entry_id, date, record_date, account, debit, credit,
counterparty, doc_id, kind, description`. Only `entry_id, date, account, debit, credit` are required;
`mapping` renames source columns. Missing kinds are inferred from the accounts and the direction of the
postings (NS 4102 numbering): a sale that debits revenue is a credit note (`sale_return`), a purchase
that debits payables a supplier credit note (`purchase_return`). Zero-amount entries are kept.
Header-only files with the required columns represent an empty dataset. Duplicate headers, column
names that collide after mapping, and rows with more or fewer fields than the header are rejected.
These column checks also apply to the economy bundle's CSV sidecars.

### SAF-T Financial

`read_saft` reads `GeneralLedgerEntries` of Norwegian SAF-T Financial files (1.10 to 1.40). Each
`Transaction` becomes an entry: `TransactionDate` is the effective date and `SystemEntryDate` (or
`GLPostingDate`) the recording date, kept as recorded even when it is earlier (such entries are flagged
as forward-dated); `CustomerID`/`SupplierID` give the counterparty and `ReferenceNumber` (else
`SourceDocumentID`) the document. All line-level invoice references are considered before source IDs;
multiple different references or counterparties are recorded as unknown rather than arbitrarily
choosing one. `SourceID` identifies the person or application entering the transaction and is never
used as an invoice identifier; missing invoice/source-document references remain unknown.
Transaction ids that repeat across journals are prefixed with the
journal id. `saft_selection` reads the period covered from the header and `bounds_between` turns it
into periods (calendar months when the year starts and ends on month boundaries, otherwise equal
periods). `write_saft` writes a minimal 1.40 file (header and general-ledger entries) that validates
against the official XSD; it is not a complete export. Given an economy's `period_bounds`, the file
covers that fiscal year: the header's `SelectionStartDate` and `SelectionEndDate` are its first and
last day, and only entries dated in it are written (including entries posted after the year end for
transactions of the year), so `veriledger check` sees the same fiscal year as the simulator.

### Economy bundles

`save_economy` writes `entries.csv` (the firm's database as it stands, i.e. after any tampering),
`parties.csv`, `mirrors.csv`, `economy.json` and, unless disabled, `truth.csv` (labels, true amounts
and dates, and the amount each entry had when it was posted). Identifiers follow recording order
(entries) and issue order (invoices). A no-truth export omits `truth.csv` and explicit tamper-event
metadata; evidence such as replicated hashes and mirrors can still reveal anomalies, so it is not a
guaranteed blind benchmark. `veriledger simulate` also writes `ledger.jsonl` (records with replicated
hashes) and `ledger.jsonl.commits.json` (commitments).
When loaded without `truth.csv`, the economy has `has_ground_truth=False`. Label-driven simulation,
evaluation, disclosure and lending reject it, as does re-exporting it with truth. Structural ledger
flags and the explicit cross-ledger `match` operation can still be used on observed records.

### Validation and format 2

All lines of one CSV entry must agree on dates, kind, counterparty, document and description. The
entry's amount must agree with its debit total, and the final cent-rounded posting lines must balance.
Invalid inputs are rejected rather than silently merged. `truth=False` removes an old `truth.csv`
when writing to a reused output directory and omits `tamper_days` from metadata. If a truth file is
present it must contain exactly one row per entry with finite non-negative amounts; malformed or
incomplete truth files are rejected. All five truth columns are required, including a nonempty
`recorded_amount`; labels must be supported schemes, and unlabelled truth must match the recorded
entry. Economy bundles also reject unbalanced postings, duplicate parties, invalid party flags,
unknown mirror owners and non-finite or negative mirror amounts. The general-ledger import itself
can return unbalanced entries; `veriledger check` reports and skips them.

Ledger JSON Lines headers now contain `format_version: 2`; commitments contain `context_hash`.
A format-1 file can be read but verifies with unbound metadata. A transition requires independent
review of the source records and period configuration before publishing new format-2 commitments.
When truth is included, economy metadata also persists `tamper_days`; older bundles infer an
alteration six days after the claimed period ends, but never earlier than the entry's recording day.
`last_day`, when present, and every `tamper_days` value must be non-negative integers, not booleans;
event keys must refer to entries present in the bundle. Empty economies retain their header and
metadata and can be saved and loaded with or without truth.

### Export validation

CSV and SAF-T writers reject invalid mutable fields and postings that do not balance after cent
rounding before opening the destination. CSV also rejects duplicate entry identifiers, which its
reader would otherwise merge. SAF-T retains separate transactions with repeated source identifiers;
its reader assigns unique identifiers without colliding with existing suffixes. Header debit/credit totals sum
those serialized lines. Export does not silently truncate fractional day offsets. Readers and `check`
can still inspect unbalanced source journals; strict writers do not export them as valid entries.

Economy saves are staged and read back for validation before the managed files are installed. Input
errors preserve existing contents, including an earlier truth sidecar; managed paths must be regular
files when present. Unrelated files are retained. Counterparty weights use 17 significant digits so
small positive values do not turn into zero probabilities. This is validation before replacement,
not a filesystem transaction spanning all files. Monetary fields remain denominated in cents.

Empty SAF-T imports produce a header-only CSV. The command determines its date origin before reading
entries and uses that same origin when writing, preferring an explicit start or the selection start.

### Ledger saves

Ledger JSON Lines and commitment sidecars retain their existing representation. Both are serialized
and written to temporary files in the destination's parent before either target is replaced. A
serialization error, missing replica hash or non-file managed target preserves existing contents.
The parent directory must exist. Final replacements are not an atomic transaction across two files.
Serializable damaged records can still be saved; use `verify` to determine integrity, independently
of whether serialization succeeded. `check` reports missing-date CSV/XML input with status 2.

---

## Python API

```python
from veriledger import (
    EconomyConfig, generate,                 # synthetic economy
    MisstatementSpec, inject,                # labelled misstatements
    Ledger, build_ledger,                    # hash-chained ledger with commitments
    SamplingAudit, LedgerAudit, CrossLedgerAudit, InterimReview, DetectionModel, CostModel,
    evaluate,                                # detection, residual misstatement, opinion and cost
    periodic, public, attested,              # disclosure regimes
    lend,                                    # inventory-backed credit
    Scenario, run, summarize,                # scenario files
)
```

Every regime has `run(econ, kind="sale", rng, detection, costs, ...) -> AuditResult`; pass the result
to `evaluate(econ, result, kind)`. `AuditResult` also carries the MUS evaluation
(`projected_misstatement`, `upper_limit`, `tolerable_amount`) and the `opinion`. Disclosure functions
return a `DisclosureResult` with daily insider and outsider paths; `lend` returns a `LendingResult`.
All functions accept an integer seed or a `numpy.random.Generator`.

`Economy.year_entries(kind, apparent=...)` gives the entries dated in the fiscal year, before or after
tampering; `Economy.fiscal_end` is the last day of the year.

Lower-level modules: `veriledger.merkle` (trees and RFC 9162 proofs), `veriledger.psi` (`intersect`,
backends), `veriledger.audit` (`mus_sample_size`, `mus_select`, `mus_evaluate`, `poisson_upper`),
`veriledger.formats` (CSV, SAF-T, bundles, fiscal periods), `veriledger.report` (figures and HTML).

### Detection, correction and assurance

`AuditResult.detected` contains discoveries; `corrected` contains adjustments actually made.
`evaluate` removes only corrected errors from the remaining balance. When creating a result manually,
supply both sets explicitly. `confirmed`, `checked`, `cleared` and `unresolved` are distinct;
`matched` is `len(confirmed)`. `SamplingAudit(correct=False)`, `LedgerAudit(correct=False)` and
`CrossLedgerAudit(correct=False)` retain discoveries without correcting them. Under cross-ledger audit,
the global switch and the nested residual sample's `correct` switch must both permit sample corrections.

`Economy.as_of(day)` includes only records entered by the decision day, mirror records dated by that
day, and database alterations whose `tamper_days` event has occurred. A mirror's economic date is
the availability proxy in this stylised model. An alteration time earlier than its entry's recording
day is treated as the recording day, including when loading older bundles.

`DisclosureResult.annual_reported` and `annual_publish_day` retain the annual publication snapshot
even when its publication falls beyond the plotted fiscal-year window.

`Ledger.verify` additionally reports `metadata_ok`, `commitment_coverage`, `uncovered_records` and
structural errors. Format-2 commitments bind the period configuration. A trusted earlier prefix does
not establish coverage of a later suffix. Format-1 files remain readable but cannot prove metadata integrity.

### Proof and commitment trust

`Ledger.verify_inclusion(record, period, proof, commitment=trusted_commitment)` requires an explicit
commitment. Verify its provenance independently before relying on the result. Passing
`ledger.commits[period]` checks only consistency with the ledger's locally held commitment. `Ledger.verify` reports
`unanchored_periods` and fails if an entry's fiscal-period commitment is absent, even when a later
checkpoint covers its hash-chain prefix; the missing commitment is needed to validate its late status.
The CLI cannot determine custody from a file path and never asserts that external data are independent.

`with_amount` supports balanced two-line entries with identifiable debit and credit sides. For a
zero-value entry it can infer sides only from the standard synthetic account pairs; ambiguous or
multi-line adjustments are rejected instead of silently changing accounting values.

`Ledger.tamper(entry_id, amount=...)` uses those same posting rules. A rejected amount edit leaves
all record fields intact, even if additional changes were supplied. Non-amount field changes can
still deliberately simulate malformed records, which verification reports as invalid. Entry public
fields are revalidated before serialization and ledger append because an `Entry` is mutable.

`make_entry` and `with_amount` require finite non-negative numeric amounts before conversion or cent
rounding; booleans and tiny negative inputs are rejected.

`CrossLedgerAudit` merges both stages' investigation counts, flags, clearances and false positives.
An exception detected by a later stage no longer remains unresolved; if `correct=False`, its known
uncorrected amount is still retained. The residual sample's unresolved amounts enter the combined
monetary upper limit once, and known-error amounts include both stages.

`lend` accumulates sampling, investigation and false-positive counts over the procedures performed
at each decision date. Discoveries persist, while current checks supersede earlier clearances and
confirmations for the checked entry. A matching record does not override a
pending investigation. Unresolved and detected-but-uncorrected entries are excluded from verified
collateral; correction retains only the substantiated true component. Earlier credit is not revised.

For directly constructed `DetectionModel` dictionaries, an omitted scheme has probability zero in
vouching, investigation and review. `DetectionModel.from_dict` instead merges supplied overrides
with the model's default probabilities, as scenario loading already did.

Monetary sampling assumes non-negative book values and overstatement errors. `mus_select` requires
a finite non-negative one-dimensional population and a non-negative integer count; a zero-value
population yields no selections. `mus_evaluate` requires taintings in [0, 1] and finite non-negative
interval, top-stratum error and known error. Invalid taintings are rejected rather than clipped.

### Publication evidence and identifiers

`periodic` scores its annual audit against the original, uncorrected snapshot at the annual publication
date, then applies the union of interim and annual corrections for outcome evaluation. A later posting
or alteration cannot retroactively create a failure of that published audit. Its result preserves the
annual audit's investigation counts, false positives and evidence sets.

Missing and whitespace-only document IDs cannot support either matching or duplicate evidence.
Nonblank IDs retain their original spelling. `inject` reserves existing entry IDs and both sides'
document references when allocating new identities; genuine duplicate schemes still copy the original
invoice reference deliberately. Neutral renumbering also skips existing references outside its invoice map, preserving their
identity. This applies with and without final neutral renumbering.

`Ledger.verify` reports non-serializable and circular record damage as errors and modifications, and
fails the chain check. It uses a placeholder only to finish the remaining checks; the ledger data are
not repaired or rewritten by verification.

### Data exchange and dates

`write_gl_csv` and `write_saft` check all entries before opening their output: public fields must be
valid and postings balanced after cent rounding. CSV requires unique entry IDs; SAF-T retains separate
transactions and its reader assigns unique IDs. A balanced raw posting that would
become unbalanced when serialized is rejected. Import readers still support inspection of unbalanced
input through `check`. SAF-T header totals sum the rounded lines actually written.

`save_economy` stages and reloads the proposed bundle before replacing its five managed files. Invalid
input and non-file managed targets are rejected before installation; unrelated files are retained.
This protects against validation failures, rather than providing a transaction across filesystem writes.
Counterparty weights use 17 significant digits; monetary values retain their cent representation.

Day and month offsets must be integers (negative offsets remain supported). Fiscal month/period counts
must be positive integers, and an explicit day range must have at least one day per period. Boolean
values are not integer parameters. The `saft-import` command returns a header-only CSV for empty input,
and returns status 2 on input errors.

### Scenario execution and ledger persistence

`run` and `run_once` validate direct Scenario objects. Audit procedures and `evaluate` use the same
configured sale/purchase target. `summarize` requires unique replication IDs within a group and
retains nullable default parameter groups. Plots show those defaults explicitly.

`Ledger.verify` returns a failed report for non-mapping record damage and continues its remaining
checks. `Ledger.save` encodes both outputs and stages them before replacing the JSON Lines file and
its commitment sidecar. Serialization and target-type errors do not first truncate saved data.
The two replacements are not a filesystem transaction, and serializable damaged records may still
be written for inspection; saving never certifies their validity.

### Numerical and evidence inputs

`poisson_upper` switches the decreasing-tail calculation at the mode and terminates each sum by a
relative precision condition. The optional `tools/check_statistics.py` checks 60 parameter combinations against SciPy, with
counts through 1,000,000; SciPy remains outside runtime dependencies.

`Economy.require_ground_truth` checks the boolean truth flag and each entry's finite nonnegative
true amount, signed integer true day and supported scheme label. Label-driven simulation and scoring
invoke it; public hashes still exclude truth. `CounterRecord` validates fields on construction.
Its document ID may be None, empty or whitespace, but missing references never support a match.

`verify_proof` accepts repeatable list/tuple paths and returns False for malformed proof containers.
`Ledger.verify_inclusion` also checks commitment integer fields, digest lengths, count consistency and
fiscal commitment dates before checking membership. Callers still supply an explicit commitment and
must establish its provenance independently. `Scenario.with_value` expands null intermediate defaults
in its copied configuration; scalar intermediate nodes raise a clear mapping error.

---

## Concepts and formulas

### Entries and mirror records

An `Entry` is a balanced double-entry journal entry with an effective (transaction) day `eff_day`, a
recording day `rec_day`, an amount, a counterparty and a document identifier. Ground-truth fields
(`true_amount`, `true_day`, `misstatement`) are used only for evaluation.

For every sale the customer holds a `CounterRecord` of the purchase, and for every purchase the
supplier holds one of the sale. With probability `mirror_noise` the counterparty dates its record one
day later, which produces genuine exceptions in cross-ledger matching.

Account numbers follow the Norwegian standard chart (NS 4102) used in SAF-T mappings: 1400 inventory,
1500 receivables, 1920 bank, 2400 payables, 3000 sales, 4000 cost of goods sold, 6300 expenses.

### Synthetic economy

The simulator generates one fiscal year, followed by the first `post_days` days of the next year, so
that cut-off at the year end can be studied. By default (`days: null`) the fiscal year is the calendar
year from `start_date` and its `periods` periods end at month ends (quarters by default), which is
what SAF-T files and `veriledger check` use; an explicit `days` gives a year of that many days split
into equal periods. Audits and disclosures cover the fiscal year; `Economy.year_entries` selects its
entries.

Daily counts of sales, purchases and expenses are Poisson with a sinusoidal seasonal factor
`1 + seasonality * sin(2 pi d / year)`. Amounts are log-normal with the given mean. Customers and
suppliers are chosen with Zipf weights; related parties also buy from the firm (with their weight)
and sell to it (`related_purchases` of purchases). Each sale posts its cost of sales at a ratio drawn
around `cost_ratio` (log-normal, `cost_sigma`). Receipts and payments follow after `payment_terms`
plus or minus five days; a share `late_payment` is paid 30 to 120 days late, a share `unpaid` is not
paid within the simulated horizon, and a share `short_payment` of customer invoices is paid only in
part. Recording lags are drawn from `record_lag_probs`. A share `adoption` of the independent
customers and of the suppliers runs the shared ledger (randomly, or the largest first with
`adoption_by_size`); related parties always do. Adoption uses its own random stream, so the
transactions are identical across adoption levels and higher shares add adopters to lower ones.

These irregularities matter when inspecting a generated ledger without its labels (`simulate
--no-truth`): unpaid, short-paid or low-margin sales and sales to related parties occur among genuine
entries too, so such signals are evidence, not proof. This toolkit's label-driven simulation and
scoring require the separate complete truth file.

Entries are numbered in recording order and invoices in issue order, also after misstatements are
injected, so identifiers do not reveal which entries are misstated. A backdated invoice keeps the
place in the sequence of the day it was issued.

### Misstatement schemes

| Scheme | Firm's record | Counterparty's record | Effect counted |
|---|---|---|---|
| `backdate` | sale from the first `backdate_window` days after a period end (including the year end) dated in the last days of that period; recorded on the true day | true date | amount shifted into the earlier period; for the annual measures only when it crosses the year end |
| `fictitious` | entry with no transaction, in the last `end_of_period_days` of a period | none | full amount |
| `inflate` | amount times `1 + inflate_factor` | invoice amount | excess |
| `duplicate` | second entry with the same document | one record | full amount |
| `round_trip` | sale to a related party, purchase back 5 to 30 days after the period end | matching (collusion) | full amount of the sale |
| `tamper` | the firm's database amount changed after the period closes; the replicated hashes keep the original | invoice amount | change |

`evaluate` reports period measures (`recall_amount`, `residual_overstatement`), in which a backdate
counts in full, and annual measures (`recall_amount_annual`, `residual_overstatement_annual`), in
which only effects on the fiscal-year total count.

### Ledger

Leaf hash `H(0x00 || record)`, chain hash `c_i = H(c_{i-1} || leaf_i)`, internal Merkle nodes
`H(0x01 || left || right)`, with an odd node carried up unchanged (the RFC 9162 tree). Inclusion
proofs are verified from the leaf index and the committed tree size, as in RFC 9162. Period `k` is
committed on day `end_k + close_lag`; the commitment holds the Merkle root, the chain head, the
number of records and a context hash of the firm, period boundaries, close lag and file-format version. Entries for a committed period are kept but marked *late*. When the
ledger is closed, a checkpoint (period index `len(period_bounds)`) commits the records posted after the
last period commitment: the simulated days of the following year, with inclusion proofs, and late
records, through the chain head. The following-year checkpoint can be updated after subsequent appends. Every record is therefore covered by a commitment. `verify(commitments)` recomputes all hashes from the
firm's records and compares them with the replicated hashes and with independently held commitments;
it reports modified records, failing periods and records missing from the firm's copy. Checks:

* integrity: records that no longer match their replicated hash;
* cut-off: records posted after the end of the period they claim (`rec_day > end(eff_day)`);
* duplicates: a document identifier repeated for the same counterparty and entry kind;
* forward-dated: records entered before the transaction date they carry (`rec_day < eff_day`), which
  occur in real data.

### Verification regimes

**Sampling** (`SamplingAudit`). Monetary-unit sample size `n = ceil(BV * RF / (TM - EM * EF))` with
`RF = -ln(1 - confidence)`, tolerable misstatement `TM = tolerable * BV` (or a given amount) and
expected misstatement `EM`; systematic selection with probability proportional to size and interval
`J = BV / n`. Attribute sampling uses `n = ceil(ln(1 - confidence) / ln(1 - tolerable_rate))`. A
cut-off test examines `cutoff_per_boundary` random entries dated in the last `cutoff_window` days of
each period. An examined misstated entry is detected with probability `vouch[scheme]`.

The sample is evaluated with the Stringer bound. For detected misstatements in sampled items below
the interval, the taintings `t = misstatement / book value` are sorted in decreasing order; then

`UML = J RF(0) + J sum_k t_(k) (RF(k) - RF(k-1)) + top + known`,

where `RF(k)` is the Poisson upper confidence limit for `k` misstatements, `top` the misstatement in
items at or above the interval and `known` the misstatement found by the cut-off test. With `correct`
(the default) management corrects the misstatements found, and the limit is reduced by those
corrections, as the AICPA audit sampling guide allows. The opinion is *clean* if the limit is at most
`TM` and *modified* otherwise. Two errors are measured: an *audit failure* (type II) is a clean opinion
although the annual overstatement left after the corrections exceeds `TM`; a *false modification*
(type I) is a modified opinion although the books were not materially misstated to begin with (annual
overstatement at most `TM`). With the default `expected = 0` the sample is planned for no
misstatement, so a single overstatement found usually leads to a modified opinion; a positive
`expected` gives a larger sample that tolerates some misstatement.

**Interim review** (`InterimReview`). Analytical procedures and inquiry without a sample: each
misstated entry of the reviewed period comes to light with probability `review[scheme]`, at
`review_per_period` hours.

**Ledger checks** (`LedgerAudit`). Every flagged entry is investigated; a misstated one is detected
with probability `investigate[scheme]`, a genuine one counts as a false positive. There is no sample
and so no opinion.

**Cross-ledger matching** (`CrossLedgerAudit`). For each adopting counterparty the auditor, acting
for the firm's side with the firm's current ledger records, and the counterparty run private set
intersection on fingerprints `doc_id | day | amount | kind | occurrence`. Occurrence counts apply
to the complete fingerprint fields, so reordering different records with the same document does not
alter matching and identical peer evidence cannot be used twice. Missing or whitespace-only
document IDs cannot support a match and are investigated as unmatched. Unmatched entries are
investigated together with the ledger flags. Entries with non-adopting counterparties are sampled with
the same absolute tolerable misstatement, and the opinion comes from that residual sample; when there is no residual sample, known uncorrected errors and unresolved flags still enter the
upper limit. Only monetary-unit residual sampling yields a monetary opinion. Successful confirmations
are counted separately from all checked records. Colluding related parties confirm their round trips.

A missing or whitespace-only document ID cannot support a duplicate allegation either. The public
ledger retains such entries because an absent reference does not establish repetition of an invoice.

Residual procedures merge investigation counts and evidence outcomes with the first stage. A later
detection or clearance resolves an earlier exception, while an uncorrected detected error remains
in the monetary bound. Amounts already included as unresolved by the residual sample are not added
again by the outer audit. Direct probability dictionaries assign zero to omitted schemes;
`DetectionModel.from_dict` merges scenario overrides with defaults.

### Private set intersection

Diffie-Hellman PSI: A sends `H(x)^a`, B returns `H(x)^{ab}` and `H(y)^b`, A computes `H(y)^{ba}`.
Backends: X25519 scalar multiplication (u-coordinates, `cryptography`), the 2048-bit MODP group of RFC
3526 (quadratic residues), and `plain` keyed hashing for fast simulation (the default of the audit
regimes). All three model set intersection in one process. They are not proven or audited privacy
protocols: both sets are present in the process, and the `plain` backend offers no privacy.

### Costs

Hours: sampling `sampling_fixed + per_item * items`; review `review_per_period`; ledger
`ledger_setup + per_entry_auto * records`; matching `psi_setup + per_psi_element * elements`;
`per_investigation` per flagged entry.

### Disclosure and the information gap

`I_d` is the true cumulative revenue on day `d`; `O_d` is the outsider's estimate.

* periodic: interim reports after each period but the last, published `report_lag` days after the
  period end following a review; the annual report, published `annual_lag` days after the year end,
  follows a sampling audit of the whole year. Uncovered days are extrapolated at the last reported
  daily rate. Review corrections enter the interim report; audit corrections only the annual report.
* public: every record from its recording day, as posted; outsiders drop repeated documents and, since
  they date records by their recording day, are not misled by backdated records posted after the end
  of the period they claim, nor by later changes to the firm's database; no amount verification.
  `pseudonymize` replaces counterparty names with pseudonyms.
* attested: daily aggregates of records as posted, published `attest_lag` days after recording. Each
  batch is checked once against mirror records available by its publication day; only discoveries
  made for that batch are reflected in its aggregate. Later evidence does not rewrite earlier releases.
  Residual monetary sampling is not supported for daily attestation.

Information-gap index `G = mean_d |O_d - I_d| / mean_d I_d` from the first report day onward.
Staleness is the revenue-weighted mean delay between a transaction and its first disclosure.
Counterparty exposure is the share of revenue whose counterparty is published by name; volume
linkability the share that outsiders can group by counterparty (also under pseudonyms).

### Lending

Reported inventory at a period end is opening inventory plus recorded purchases minus cost of sales.
Credit is `collateral * (v * advance_verified + (1 - v) * advance_unverified)`, where `v` is the share
of purchases verified: the examined pool under the stylised field-exam policy, and purchases matched
with suppliers or cleared by investigation under the ledger policy. Pending exceptions and detected
but uncorrected errors are excluded from both verified pools. Over-advance and
shortfall are measured against `advance_verified * true inventory`.

### Corrections and publication time

Detection does not itself change a financial statement. An adjustment must be in `corrected` before
it reduces remaining misstatement. The final opinion follows all sampling and duplicate procedures.
Cross-ledger upper limits include known errors not corrected and the full book amount of unresolved
exceptions. This is conservative assurance; it does not remove collusion confirmed by counterparties.

Interim publications use their own date-limited snapshots. The annual sample uses the books after
interim corrections, and its annual publication incorporates corrections from every quarter. The
fiscal-year information-gap window is unchanged; annual disclosure normally occurs after it.

Periodic annual audit scores use the original books visible on that annual publication date, with
interim and annual corrections applied as outcomes. Later postings or alterations are absent from
that audit's population and ground-truth labels. Investigation and false-positive counts reflect the
annual procedures actually performed.

Credit is decided at each period end from entries with `rec_day <= decision_day` and mirror records
available then. Corrections discovered later never revise past credit. The true component of a
corrected inflated purchase remains verifiable collateral; a fictitious purchase contributes zero.
Ledger and PSI set-up costs are paid once per year; each quarterly decision rechecks the cumulative
available population. Field examinations cover newly available purchases since the preceding exam.
Procedure counts accumulate across examinations and rechecks. Detected/corrected discoveries persist;
confirmations reflect the latest relevant matching check, and a current unresolved investigation
supersedes an earlier clearance. Thus evidence on an original amount cannot certify a later alteration.
Opening inventory is an ex ante model calibration shared by policies, not a source of matching evidence.
Ordinary posting delays can create over-advance or shortfall even in otherwise clean books.

### Valid evaluation data and evidence

A truth-presence flag alone cannot establish valid evaluation input. Simulation and scoring require
finite nonnegative true amounts, integer true dates and known scheme labels; booleans are not dates.
These fields remain outside public commitments. Counterparty observations have the same discrete
calendar and finite-amount requirements, while missing references remain ambiguous evidence.

Inclusion verification checks the supplied commitment's structure and a repeatable proof path.
An iterator cannot stand in for a path that is read both during validation and during verification.
A successful membership check does not establish transaction truth or independent custody.

---

## Validation

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

### Running the tests

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

---

## Limitations

* The economy, detection probabilities, review and audit costs are stylised; results show mechanisms,
  not estimates for a particular market.
* Detection is driven by the misstatement labels: a procedure that examines a misstated entry finds it
  with a given probability. The audit opinion follows a single rule (the Stringer bound against
  tolerable misstatement); extended testing, adjustments negotiated with management and other
  evidence are not modelled.
* Counterparty records are simulated. Real data rarely include both sides of a transaction.
* Generated ledgers contain realistic red flags (unpaid or short-paid invoices, low margins, sales to
  related parties, entries near period ends), and these are more frequent among misstated entries than
  among genuine ones. They are evidence, not proof, but a benchmark built on generated data should
  report how much a method gains over such simple signals.
* All matching executes in one process with both parties' data present. No security guarantee is
  established, even against semi-honest parties. The X25519 mapping is non-standard and the default
  `plain` backend provides no privacy. Do not use this code to protect confidential data.
* Collusion is modelled only for related parties; consensus, network latency and smart-contract
  execution are not modelled.
* Returns and credit notes are recognised on import but not simulated.
* The information-gap index combines timing and level errors, so a disclosure delay can partly offset
  an undetected overstatement.
* The SAF-T writer produces a minimal file for testing, not a complete statutory export.

* Mirror economic dates proxy availability; separate transmission and investigation-completion delays
  are not modelled. Quarterly decisions therefore use explicit recording cut-offs but not a network simulator.
* Daily attestation examines each day's records once using the mirror records available that day.
  Later evidence does not cause a restatement or a repeat investigation. Peer candidates are selected
  in process; reported PSI operations and costs are simulation estimates, not a network protocol.
  Daily attestation does not support residual monetary sampling.
* An unresolved flagged item contributes its entire book amount to the monetary upper limit; this
  conservative treatment may increase modified opinions.
* Quarterly lending rechecks cumulative records and charges repeated processing/investigation hours;
  it does not optimise a production auditor's exception-management workflow.
* A no-truth export omits explicit labels and alteration timing, but ledger hashes and counterparty
  mirrors may still reveal anomalies. It is not guaranteed to be blind ground-truth-free data.
  Simulation and scoring require a complete, valid truth file and do not treat its absence as evidence
  that observed transactions are correct.
