"""Example 3: inventory-backed credit with verifiable purchase records (Chod et al., 2020).

Usage: python examples/03_inventory_lending.py [--out DIR] [--replications N]
"""

import argparse
from pathlib import Path

from _common import line, plt, run_bundled, save

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="example-output/03_lending")
ap.add_argument("--replications", type=int, default=None)
ap.add_argument("--plot-only", action="store_true", help="reuse saved results.csv")
args = ap.parse_args()
out = Path(args.out)
scn, df, s, key = run_bundled("lending_inventory", out, args.replications, args.plot_only)

fig, axs = plt.subplots(1, 2, figsize=(6.8, 3.0))
for regime in ("trust", "haircut", "field_exam", "ledger"):
    sk, lab = ("ledger_policy", "Ledger verification") if regime == "ledger" else (None, None)
    line(axs[0], s, key, regime, "over_advance", 1e-3, sk, lab)
    line(axs[1], s, key, regime, "shortfall", 1e-3, sk, lab)
axs[0].set_ylabel("Over-advance (thousands)")
axs[0].set_title("(a) Lender exposure", fontsize=10)
axs[1].set_ylabel("Credit shortfall (thousands)")
axs[1].set_title("(b) Financing withheld", fontsize=10)
for ax in axs:
    ax.set_xlabel("Supplier adoption share")
    ax.set_ylim(bottom=0)
axs[0].legend(fontsize=8, loc="center right", bbox_to_anchor=(1.0, 0.30))
save(fig, out, "fig_lending")
cols = [key, "regime", "credit", "over_advance", "shortfall", "verified_share", "cost_hours"]
print(s[cols].round(1).to_string(index=False))
