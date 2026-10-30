# Python API

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

## Detection, correction and assurance

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

## Proof and commitment trust

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

## Publication evidence and identifiers

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

## Data exchange and dates

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

## Scenario execution and ledger persistence

`run` and `run_once` validate direct Scenario objects. Audit procedures and `evaluate` use the same
configured sale/purchase target. `summarize` requires unique replication IDs within a group and
retains nullable default parameter groups. Plots show those defaults explicitly.

`Ledger.verify` returns a failed report for non-mapping record damage and continues its remaining
checks. `Ledger.save` encodes both outputs and stages them before replacing the JSON Lines file and
its commitment sidecar. Serialization and target-type errors do not first truncate saved data.
The two replacements are not a filesystem transaction, and serializable damaged records may still
be written for inspection; saving never certifies their validity.

## Numerical and evidence inputs

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
