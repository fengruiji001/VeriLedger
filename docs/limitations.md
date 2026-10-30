# Limitations

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
