# Scenario files

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

## Audit populations and replication groups

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

## Nested null defaults

An explicitly null nested default, such as `regime_params.crossledger.residual: null`, may be expanded
when sweeping `regime_params.crossledger.residual.confidence`. The variant receives a copied mapping
and normal default validation; the original Scenario remains unchanged. A path through a scalar
field raises ValueError rather than a dictionary assignment TypeError.
