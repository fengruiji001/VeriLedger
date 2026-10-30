"""Figures (black and white) and a self-contained HTML report for scenario results."""

from __future__ import annotations

import base64
import html
import io
from pathlib import Path

import pandas as pd

STYLES = ["-", "--", "-.", ":"]
MARKERS = ["o", "s", "^", "D"]
LABELS = {
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
#: labels that differ by experiment
EXPERIMENT_LABELS = {("lending", "ledger"): "Ledger verification"}
METRICS = {
    "audit": [
        ("recall_amount_annual", "Share of annual overstatement detected"),
        ("cost_hours", "Audit cost (hours)"),
    ],
    "disclosure": [
        ("gap_index", "Information-gap index"),
        ("residual_overstatement_annual", "Remaining annual overstatement"),
    ],
    "lending": [("over_advance", "Over-advance (currency units)"), ("shortfall", "Credit shortfall (currency units)")],
}


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

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
    return plt


def plot_summary(summary: pd.DataFrame, experiment: str, sweep_key: str | None, path: str | Path | None = None):
    """Two-panel figure of the main metrics against the sweep parameter (or by regime)."""
    plt = _plt()
    metrics = METRICS[experiment]
    fig, axs = plt.subplots(1, len(metrics), figsize=(6.8, 3.0))
    regimes = list(dict.fromkeys(summary["regime"]))
    categorical = bool(sweep_key) and (
        not pd.api.types.is_numeric_dtype(summary[sweep_key]) or summary[sweep_key].isna().any()
    )
    categories = summary[sweep_key].drop_duplicates().tolist() if categorical else []
    positions = {None if pd.isna(value) else value: i for i, value in enumerate(categories)}
    for ax, (m, label) in zip(axs, metrics):
        if m not in summary:
            ax.set_visible(False)
            continue
        if sweep_key:
            for i, r in enumerate(regimes):
                d = summary[summary["regime"] == r].sort_values(sweep_key)
                if categorical:
                    x = d[sweep_key].map(lambda value: positions[None if pd.isna(value) else value])
                    order = x.argsort()
                    d, x = d.iloc[order], x.iloc[order]
                else:
                    x = d[sweep_key]
                ax.errorbar(
                    x,
                    d[m],
                    yerr=d.get(f"{m}_se"),
                    color="black",
                    ls=STYLES[i % 4],
                    marker=MARKERS[i % 4],
                    ms=4,
                    lw=1.3,
                    capsize=2,
                    label=EXPERIMENT_LABELS.get((experiment, r), LABELS.get(r, r)),
                    mfc="white" if i % 2 else "black",
                )
            if categorical:
                ax.set_xticks(
                    range(len(categories)), ["default" if pd.isna(value) else str(value) for value in categories]
                )
            ax.set_xlabel(sweep_key.split(".")[-1].replace("_", " ").capitalize())
        else:
            d = summary.set_index("regime").loc[regimes]
            ax.bar(range(len(regimes)), d[m], yerr=d.get(f"{m}_se"), color="0.75", edgecolor="black", capsize=3)
            names = [EXPERIMENT_LABELS.get((experiment, r), LABELS.get(r, r)) for r in regimes]
            ax.set_xticks(range(len(regimes)), names, rotation=15, ha="right")
        ax.set_ylabel(label)
    if sweep_key:
        legend: dict[str, object] = {}
        for ax in axs:
            if ax.get_visible():
                handles, labels = ax.get_legend_handles_labels()
                legend.update(zip(labels, handles))
        if legend:
            fig.legend(legend.values(), legend, loc="upper center", ncol=len(legend), fontsize=8, frameon=False)
        fig.tight_layout(rect=(0, 0, 1, 0.92 if legend else 1))
    else:
        fig.tight_layout()
    if path:
        fig.savefig(path, bbox_inches="tight")
        if str(path).endswith(".png"):
            fig.savefig(str(path)[:-4] + ".pdf", bbox_inches="tight")
    return fig


def _img(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    return base64.b64encode(buf.getvalue()).decode()


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.4g}"


def html_report(title: str, scenario: dict, summary: pd.DataFrame, figures: list, path: str | Path) -> None:
    """Write a single-file HTML report (embedded images, no external resources)."""
    cols = [c for c in summary.columns if not c.endswith("_se")]
    table = summary[cols].to_html(index=False, float_format=_fmt, border=0, classes="t")
    imgs = "".join(f'<img alt="figure" src="data:image/png;base64,{_img(f)}">' for f in figures)
    import yaml

    cfg = html.escape(yaml.safe_dump(scenario, sort_keys=False))
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{font-family:Georgia,serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#111;background:#fff}}
table.t{{border-collapse:collapse;font-size:13px;display:block;overflow-x:auto}}
table.t th,table.t td{{border-bottom:1px solid #ccc;padding:4px 8px;text-align:right}}
img{{max-width:100%;margin:12px 0}}pre{{background:#f4f4f4;padding:10px;overflow-x:auto;font-size:12px}}</style>
</head><body><h1>{html.escape(title)}</h1><p>Generated by VeriLedger. Values are means over replications.</p>
{imgs}<h2>Summary</h2>{table}<h2>Scenario</h2><pre>{cfg}</pre></body></html>"""
    Path(path).write_text(doc, encoding="utf-8")
