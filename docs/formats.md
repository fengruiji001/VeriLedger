# File formats

## General-ledger CSV

One row per posting line with columns `entry_id, date, record_date, account, debit, credit,
counterparty, doc_id, kind, description`. Only `entry_id, date, account, debit, credit` are required;
`mapping` renames source columns. Missing kinds are inferred from the accounts and the direction of the
postings (NS 4102 numbering): a sale that debits revenue is a credit note (`sale_return`), a purchase
that debits payables a supplier credit note (`purchase_return`). Zero-amount entries are kept.
Header-only files with the required columns represent an empty dataset. Duplicate headers, column
names that collide after mapping, and rows with more or fewer fields than the header are rejected.
These column checks also apply to the economy bundle's CSV sidecars.

## SAF-T Financial

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

## Economy bundles

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

## Validation and format 2

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

## Export validation

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

## Ledger saves

Ledger JSON Lines and commitment sidecars retain their existing representation. Both are serialized
and written to temporary files in the destination's parent before either target is replaced. A
serialization error, missing replica hash or non-file managed target preserves existing contents.
The parent directory must exist. Final replacements are not an atomic transaction across two files.
Serializable damaged records can still be saved; use `verify` to determine integrity, independently
of whether serialization succeeded. `check` reports missing-date CSV/XML input with status 2.
