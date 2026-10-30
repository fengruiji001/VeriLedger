"""Example 1: information gap and counterparty exposure under three disclosure regimes (Yermack, 2017).

Usage: python examples/01_disclosure_regimes.py [--out DIR] [--replications N]
"""

import argparse
from pathlib import Path

import numpy as np
from _common import NAMES, STYLE, line, plt, run_bundled, save

from veriledger.disclosure import attested, periodic, public
from veriledger.misstate import MisstatementSpec, inject
from veriledger.synth import EconomyConfig, generate

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="example-output/01_disclosure")
ap.add_argument("--replications", type=int, default=None)
ap.add_argument("--plot-only", action="store_true", help="reuse saved results.csv")
args = ap.parse_args()
out = Path(args.out)
scn, df, s, key = run_bundled("disclosure_regimes", out, args.replications, args.plot_only)

# one replication at 50% adoption for the time paths
rng = np.random.default_rng(scn.seed)
econ = inject(
    generate(EconomyConfig(**{**scn.economy, "adoption": 0.5}), rng), MisstatementSpec(**scn.misstatements), rng
)
paths = {"periodic": periodic(econ, rng=1), "public": public(econ), "attested": attested(econ, rng=1)}

fig, axs = plt.subplots(1, 2, figsize=(6.8, 3.0))
for name, r in paths.items():
    ls, _, _ = STYLE[name]
    d = r.days[r.window_start :]
    err = (r.outsider[r.window_start :] - r.insider[r.window_start :]) / r.insider[r.window_start :]
    axs[0].plot(d, 100 * err, color="black", ls=ls, lw=1.3, label=NAMES[name])
axs[0].axhline(0, color="0.6", lw=0.6)
axs[0].set_xlabel("Day of fiscal year")
axs[0].set_ylabel("Outsider error (% of true revenue)")
axs[0].set_title("(a) One simulated year, 50% adoption", fontsize=10)
axs[0].legend(fontsize=8, loc="upper left")
for regime in ("periodic", "public", "attested"):
    line(axs[1], s, key, regime, "gap_index", 100)
axs[1].set_xlabel("Counterparty adoption share")
axs[1].set_ylabel("Information-gap index (%)")
axs[1].set_title("(b) Mean over replications", fontsize=10)
axs[1].set_ylim(bottom=0)
axs[1].legend(fontsize=8, loc="center right")
save(fig, out, "fig_disclosure")
cols = ["economy.adoption", "regime", "gap_index", "staleness_days", "counterparty_exposure", "residual_overstatement"]
cols += ["residual_overstatement_annual", "clean_opinion", "cost_hours"]
print(s[cols].round(4).to_string(index=False))
