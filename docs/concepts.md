# Concepts and formulas

## Entries and mirror records

An `Entry` is a balanced double-entry journal entry with an effective (transaction) day `eff_day`, a
recording day `rec_day`, an amount, a counterparty and a document identifier. Ground-truth fields
(`true_amount`, `true_day`, `misstatement`) are used only for evaluation.

For every sale the customer holds a `CounterRecord` of the purchase, and for every purchase the
supplier holds one of the sale. With probability `mirror_noise` the counterparty dates its record one
day later, which produces genuine exceptions in cross-ledger matching.

Account numbers follow the Norwegian standard chart (NS 4102) used in SAF-T mappings: 1400 inventory,
1500 receivables, 1920 bank, 2400 payables, 3000 sales, 4000 cost of goods sold, 6300 expenses.

## Synthetic economy

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

## Misstatement schemes

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

## Ledger

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

## Verification regimes

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

## Private set intersection

Diffie-Hellman PSI: A sends `H(x)^a`, B returns `H(x)^{ab}` and `H(y)^b`, A computes `H(y)^{ba}`.
Backends: X25519 scalar multiplication (u-coordinates, `cryptography`), the 2048-bit MODP group of RFC
3526 (quadratic residues), and `plain` keyed hashing for fast simulation (the default of the audit
regimes). All three model set intersection in one process. They are not proven or audited privacy
protocols: both sets are present in the process, and the `plain` backend offers no privacy.

## Costs

Hours: sampling `sampling_fixed + per_item * items`; review `review_per_period`; ledger
`ledger_setup + per_entry_auto * records`; matching `psi_setup + per_psi_element * elements`;
`per_investigation` per flagged entry.

## Disclosure and the information gap

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

## Lending

Reported inventory at a period end is opening inventory plus recorded purchases minus cost of sales.
Credit is `collateral * (v * advance_verified + (1 - v) * advance_unverified)`, where `v` is the share
of purchases verified: the examined pool under the stylised field-exam policy, and purchases matched
with suppliers or cleared by investigation under the ledger policy. Pending exceptions and detected
but uncorrected errors are excluded from both verified pools. Over-advance and
shortfall are measured against `advance_verified * true inventory`.

## Corrections and publication time

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

## Valid evaluation data and evidence

A truth-presence flag alone cannot establish valid evaluation input. Simulation and scoring require
finite nonnegative true amounts, integer true dates and known scheme labels; booleans are not dates.
These fields remain outside public commitments. Counterparty observations have the same discrete
calendar and finite-amount requirements, while missing references remain ambiguous evidence.

Inclusion verification checks the supplied commitment's structure and a repeatable proof path.
An iterator cannot stand in for a path that is read both during validation and during verification.
A successful membership check does not establish transaction truth or independent custody.
