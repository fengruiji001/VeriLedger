"""Example 2: detection, audit cost and audit opinions as counterparties adopt the shared ledger
(Cao, Cong & Yang, 2025), and what collusion does to the opinion (Cong & He, 2019).

Usage: python examples/02_audit_adoption.py [--out DIR] [--replications N] [--plot-only]
"""

import argparse
from pathlib import Path

from _common import line, plt, run_bundled, save

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="example-output/02_audit")
ap.add_argument("--replications", type=int, default=None)
ap.add_argument("--plot-only", action="store_true", help="reuse saved results.csv")
args = ap.parse_args()
out = Path(args.out)
scn, df, s, key = run_bundled("audit_adoption", out, args.replications, args.plot_only)
_, dfc, sc, keyc = run_bundled("audit_collusion", out / "collusion", args.replications, args.plot_only)

fig, grid = plt.subplots(2, 2, figsize=(6.8, 5.6))
axs = grid.ravel()
for regime in ("sampling", "ledger", "crossledger"):
    line(axs[0], s, key, regime, "recall_amount_annual", 100)
    line(axs[1], s, key, regime, "cost_hours")
for regime in ("sampling", "crossledger"):
    line(axs[2], sc, keyc, regime, "audit_failure", 100)
    line(axs[3], sc, keyc, regime, "false_modification", 100)
axs[0].set_ylabel("Annual overstatement detected (%)")
axs[0].set_ylim(0, 100)
axs[0].set_title("(a) Detection", fontsize=10)
axs[1].set_ylabel("Audit cost (hours)")
axs[1].set_ylim(bottom=0)
axs[1].set_title("(b) Cost", fontsize=10)
for ax in axs[:2]:
    ax.set_xlabel("Counterparty adoption share")
axs[2].set_ylabel("Replications (%)")
axs[2].set_title("(c) Clean opinion, material misstatement", fontsize=10)
axs[3].set_title("(d) Modified opinion, books not material", fontsize=10)
for ax in axs[2:]:
    ax.set_xlabel("Colluding round trips (full adoption)")
    ax.set_xticks([0, 8, 16, 24, 32])
    ax.set_ylim(-5, 105)
handles, labels = axs[0].get_legend_handles_labels()
fig.legend(handles, labels, loc="upper center", ncol=3, fontsize=8, frameon=False, bbox_to_anchor=(0.5, 1.03))
save(fig, out, "fig_audit")
cols = [key, "regime", "recall_amount_annual", "recall_amount", "cost_hours", "false_positives"]
cols += ["residual_overstatement_annual", "tolerable_amount", "upper_limit", "clean_opinion", "audit_failure"]
cols += ["false_modification"]
print(s[cols].round(3).to_string(index=False))
schemes = [f"recall_{x}" for x in ("backdate", "fictitious", "inflate", "duplicate", "round_trip", "tamper")]
print(s[s[key] == 0.5][["regime"] + schemes].round(3).to_string(index=False))
colsc = [keyc, "regime", "amount_misstated_annual", "residual_overstatement_annual", "tolerable_amount"]
colsc += ["clean_opinion", "audit_failure", "false_modification"]
print(sc[colsc].round(3).to_string(index=False))
