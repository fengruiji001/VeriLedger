"""Draw the architecture figure used in the documentation and the software paper."""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

plt.rcParams.update({"font.family": "DejaVu Serif", "savefig.dpi": 300})
out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/figures")
fig, ax = plt.subplots(figsize=(7.2, 4.1))
ax.set_xlim(0, 12)
ax.set_ylim(0, 7)
ax.axis("off")


def box(x, y, w, h, title, body, fill="white", dashed=False):
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.02,rounding_size=0.12",
            fc=fill,
            ec="black",
            lw=1.0,
            ls="--" if dashed else "-",
        )
    )
    ax.text(x + w / 2, y + h - 0.2, title, ha="center", va="top", fontsize=9, fontweight="bold")
    if body:
        ax.text(x + w / 2, y + h - 0.55, body, ha="center", va="top", fontsize=7.6, linespacing=1.3)


def arrow(x1, y1, x2, y2):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=10, lw=1.0, color="black"))


# column headers
for x, t in ((1.6, "Inputs"), (6.0, "Simulation and verification"), (10.4, "Outputs")):
    ax.text(x, 6.85, t, ha="center", va="top", fontsize=9.5, style="italic")
# inputs
box(0.1, 4.6, 3.0, 1.9, "Scenario (YAML)", "economy, misstatements,\nregimes, costs,\nparameter sweep")
box(0.1, 2.4, 3.0, 1.9, "synth", "fiscal year and next\ndays, counterparties,\nmirror records, adoption")
box(0.1, 0.2, 3.0, 1.9, "formats", "general-ledger CSV,\nSAF-T Financial XML,\nchecks of real ledgers")
# core
box(
    3.9,
    4.6,
    4.2,
    1.9,
    "misstate",
    "backdate (also across the year end),\nfictitious, inflate, duplicate,\nround trip, tamper (labelled)",
    fill="0.93",
)
box(
    3.9,
    2.4,
    4.2,
    1.9,
    "ledger  +  merkle",
    "hash chain, fiscal context,\ncommitments, RFC 9162 proofs,\nintegrity, cut-off and duplicates",
    fill="0.93",
)
box(
    3.9,
    0.2,
    4.2,
    1.9,
    "audit  +  psi",
    "sampling with opinion, reviews,\nledger checks, cross-ledger matching\nPSI-style simulation",
    fill="0.93",
)
# outputs
box(8.9, 4.6, 3.0, 1.9, "disclosure, lending", "dated disclosure,\nquarterly snapshots,\ncredit and over-advance")
box(8.9, 2.4, 3.0, 1.9, "metrics", "detection and correction,\nannual misstatement,\nopinions, hours")
box(8.9, 0.2, 3.0, 1.9, "scenario, report, CLI", "replications, sweeps,\nCSV, figures,\nHTML report", dashed=True)
# arrows
arrow(3.1, 5.55, 3.9, 5.55)
arrow(3.1, 3.35, 3.9, 5.2)
arrow(3.1, 1.15, 3.9, 3.0)
arrow(6.0, 4.6, 6.0, 4.3)
arrow(6.0, 2.4, 6.0, 2.1)
arrow(8.1, 1.15, 8.9, 3.0)
arrow(8.1, 3.35, 8.9, 5.2)
arrow(10.4, 2.4, 10.4, 2.1)
arrow(10.4, 4.6, 10.4, 4.3)
fig.tight_layout()
out.mkdir(parents=True, exist_ok=True)
fig.savefig(out / "architecture.png", bbox_inches="tight")
fig.savefig(out / "architecture.pdf", bbox_inches="tight")
