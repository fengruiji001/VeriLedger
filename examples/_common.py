"""Shared helpers for the example scripts (plot style and scenario runner)."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from veriledger.cli import bundled_scenario  # noqa: E402
from veriledger.scenario import Scenario, run, summarize  # noqa: E402

plt.rcParams.update(
    {
        "font.family": "DejaVu Serif",
        "font.size": 10,
        "legend.frameon": True,
        "legend.facecolor": "white",
        "legend.edgecolor": "none",
        "savefig.dpi": 300,
    }
)
STYLE = {
    "sampling": ("-", "o", "black"),
    "ledger": ("--", "s", "white"),
    "crossledger": ("-.", "^", "black"),
    "periodic": ("-", "o", "black"),
    "public": ("--", "s", "white"),
    "attested": ("-.", "^", "black"),
    "trust": ("-", "o", "black"),
    "haircut": (":", "D", "white"),
    "field_exam": ("--", "s", "white"),
    "ledger_policy": ("-.", "^", "black"),
}
NAMES = {
    "sampling": "Sampling audit",
    "ledger": "Ledger checks",
    "crossledger": "Cross-ledger matching",
    "periodic": "Periodic reports",
    "public": "Public ledger",
    "attested": "Attested ledger",
    "trust": "Trust reports",
    "haircut": "Haircut",
    "field_exam": "Field exam",
}


def run_bundled(name: str, out: Path, replications: int | None = None, plot_only: bool = False):
    """Run a bundled scenario (or, with ``plot_only``, reload its saved results)."""
    import pandas as pd

    scn = Scenario.from_yaml(bundled_scenario(name))
    if replications:
        scn.replications = replications
    key = next(iter(scn.sweep))
    if plot_only and (out / "results.csv").exists():
        df = pd.read_csv(out / "results.csv")
        return scn, df, summarize(df, key), key
    df = run(scn)
    s = summarize(df, key)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "results.csv", index=False)
    s.to_csv(out / "summary.csv", index=False)
    return scn, df, s, key


def line(ax, s, key, regime, metric, scale=1.0, style_key=None, label=None):
    ls, mk, face = STYLE[style_key or regime]
    d = s[s["regime"] == regime].sort_values(key)
    ax.errorbar(
        d[key],
        d[metric] * scale,
        yerr=d[f"{metric}_se"] * scale,
        color="black",
        ls=ls,
        marker=mk,
        ms=5,
        mfc=face,
        lw=1.3,
        capsize=2,
        label=label or NAMES.get(regime, regime),
    )


def save(fig, out: Path, name: str):
    fig.tight_layout()
    fig.savefig(out / f"{name}.png", bbox_inches="tight")
    fig.savefig(out / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)
