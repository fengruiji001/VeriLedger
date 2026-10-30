"""Quick start: one economy, three audit regimes, one table."""

import pandas as pd

from veriledger import CrossLedgerAudit, EconomyConfig, LedgerAudit, SamplingAudit, evaluate, generate, inject

econ = generate(EconomyConfig(adoption=0.5), seed=1)
econ = inject(
    econ,
    {"backdate": 0.3, "fictitious": 0.01, "inflate": 0.01, "duplicate": 0.005, "round_trip": 4, "tamper": 0.005},
    seed=2,
)
rows = [evaluate(econ, regime.run(econ, rng=3)) for regime in (SamplingAudit(), LedgerAudit(), CrossLedgerAudit())]
cols = ["regime", "n_misstated", "recall", "recall_amount_annual", "false_positives", "opinion", "cost_hours"]
print(pd.DataFrame(rows)[cols].round(3).to_string(index=False))
