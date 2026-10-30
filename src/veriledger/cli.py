"""Command-line interface: ``veriledger <command> [options]``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from importlib import resources
from pathlib import Path

from . import __version__

BUNDLED = ("audit_adoption", "audit_collusion", "disclosure_regimes", "lending_inventory")


def bundled_scenario(name: str) -> Path:
    return Path(str(resources.files("veriledger") / "data" / f"{name}.yaml"))


def cmd_run(args) -> int:
    from .report import html_report, plot_summary
    from .scenario import Scenario, run, summarize

    scn = Scenario.from_yaml(args.config)
    if args.replications is not None:
        scn.replications = args.replications
        scn.validate()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    df = run(scn, progress=not args.quiet)
    key = next(iter(scn.sweep), None)
    summary = summarize(df, key)
    df.to_csv(out / "results.csv", index=False)
    summary.to_csv(out / "summary.csv", index=False)
    fig = plot_summary(summary, scn.experiment, key, out / "summary.png")
    html_report(scn.name, scn.to_dict(), summary, [fig], out / "report.html")
    if not args.quiet:
        print(f"{scn.name}: {len(df)} rows in {time.perf_counter() - t0:.1f}s -> {out}")
    return 0


def cmd_demo(args) -> int:
    out = Path(args.out)
    for name in BUNDLED:
        ns = argparse.Namespace(
            config=bundled_scenario(name), out=out / name, quiet=args.quiet, replications=args.replications
        )
        cmd_run(ns)
    return 0


def cmd_simulate(args) -> int:
    import numpy as np
    import yaml

    from .audit import build_ledger
    from .formats import save_economy
    from .misstate import MisstatementSpec, inject
    from .synth import EconomyConfig, generate

    if args.scenario:  # the exact economy that one replication of a scenario uses
        from .scenario import Scenario, economy_for

        scn = Scenario.from_yaml(args.scenario)
        if args.sweep_value is not None:
            ((key, _),) = scn.sweep.items() if scn.sweep else ((None, None),)
            if key is None:
                raise SystemExit("error: --sweep-value given but the scenario has no sweep")
            scn = scn.with_value(key, yaml.safe_load(args.sweep_value))
        econ = economy_for(scn, args.replication)
    else:
        cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) if args.config else {}
        rng = np.random.default_rng(args.seed)
        econ = generate(EconomyConfig.from_dict(cfg.get("economy")), rng)
        econ = inject(econ, MisstatementSpec.from_dict(cfg.get("misstatements")), rng)
    out = Path(args.out)
    save_economy(econ, out, truth=not args.no_truth)
    build_ledger(econ).save(out / "ledger.jsonl")
    print(f"{len(econ.entries)} entries, {len(econ.misstated())} misstated -> {out}")
    return 0


def cmd_verify(args) -> int:
    from .ledger import Commitment, Ledger

    try:
        led = Ledger.load(args.ledger)
        commits = None
        if args.commits:
            raw = json.loads(Path(args.commits).read_text(encoding="utf-8"))
            if not isinstance(raw, list) or not raw:
                raise ValueError("independent commitments must be a non-empty list")
            commits = {c.period: c for c in map(Commitment.from_dict, raw)}
            if len(commits) != len(raw):
                raise ValueError("duplicate commitment period")
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"error: cannot read ledger or commitments: {exc}", file=sys.stderr)
        return 2
    if commits is None:
        print(
            "note: no independent commitments given (--commits); the ledger is checked only against the "
            "commitments stored next to it, which whoever controls the file can rewrite",
            file=sys.stderr,
        )
    else:
        print(
            "note: a supplied commitments file is not proof of independent custody; verify its provenance separately",
            file=sys.stderr,
        )
    try:
        rep = led.verify(commits)
        result = {
            "records": rep.n_records,
            "ok": rep.ok,
            "external_commitments_supplied": commits is not None,
            "independent_commitments": None,  # provenance cannot be inferred from a path
            "trust_verified": False,
            "modified_records": [led.records[i]["entry_id"] for i in rep.modified],
            "missing_records": rep.missing_records,
            "metadata_ok": rep.metadata_ok,
            "commitment_coverage": rep.commitment_coverage,
            "uncovered_records": rep.uncovered_records,
            "unanchored_periods": rep.unanchored_periods,
            "errors": rep.errors,
            "periods_failing_commitment": rep.bad_periods,
            "late_records": len(led.late),
            "cutoff_flags": len(led.cutoff_flags()),
            "duplicate_flags": len(led.duplicate_flags()),
            "forward_dated_flags": len(led.forward_flags()),
        }
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        print(f"error: invalid ledger data: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if rep.ok else 1


def cmd_saft_import(args) -> int:
    import xml.etree.ElementTree as ET

    from .formats import read_saft, saft_selection, write_gl_csv

    try:
        selection = saft_selection(args.file)
        start = args.start or (selection[0] if selection else min(_iso_dates(args.file), default=None))
        entries = read_saft(args.file, start)
        if start is None:  # an empty file with no dates only produces a CSV header
            write_gl_csv(entries, args.out)
        else:
            write_gl_csv(entries, args.out, start)
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{len(entries)} transactions -> {args.out}")
    return 0


def _iso_dates(path):
    import xml.etree.ElementTree as ET

    from .formats import _strip

    return [el.text[:10] for el in ET.parse(str(path)).getroot().iter() if _strip(el.tag) == "TransactionDate"]


def cmd_check(args) -> int:
    """Record an imported general ledger on a ledger and run the ledger checks."""
    import csv
    import xml.etree.ElementTree as ET

    from .formats import add_months, bounds_between, read_gl_csv, read_saft, saft_selection, to_date, to_day
    from .ledger import Ledger

    path = Path(args.file)
    is_xml = path.suffix.lower() == ".xml"
    try:
        # the fiscal year: --start/--end, else the SAF-T selection dates, else twelve months from the
        # first transaction date
        sel = saft_selection(path) if is_xml else None
        start = args.start or (sel[0] if sel else None)
        if start is None:
            start = min(_iso_dates(path)) if is_xml else min(_csv_dates(path))
        end = args.end or (sel[1] if sel and not args.start else None)
        if end is None:
            end = to_date(start, to_day(start, add_months(start, 12)) - 1)
        bounds = bounds_between(start, end, args.periods)
        entries = read_saft(path, start) if is_xml else read_gl_csv(path, start)
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    unbalanced = [e for e in entries if not e.is_balanced()]
    before = [e for e in entries if e.eff_day < 0]
    usable = [e for e in entries if e.is_balanced() and e.eff_day >= 0]
    try:
        led = Ledger.from_entries(path.stem, usable, bounds, args.close_lag)
    except (ValueError, TypeError, KeyError) as exc:
        print(f"error: cannot record imported entries: {exc}", file=sys.stderr)
        return 2
    flags: dict[int, list[str]] = {}
    for name, idx in (
        ("cutoff", led.cutoff_flags()),
        ("duplicate", led.duplicate_flags()),
        ("forward_dated", led.forward_flags()),
    ):
        for i in idx:
            flags.setdefault(i, []).append(name)
    kinds: dict[str, int] = {}
    for e in usable:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    year_end = end
    summary = {
        "file": path.name,
        "fiscal_year": [start, year_end],
        "periods": [[to_date(start, a), to_date(start, b)] for a, b in bounds],
        "entries": len(entries),
        "recorded": len(led.records),
        "unbalanced_skipped": [e.entry_id for e in unbalanced],
        "before_start_skipped": len(before),
        "after_year_end": sum(1 for r in led.records if r["eff_day"] > bounds[-1][1]),
        "kinds": dict(sorted(kinds.items())),
        "cutoff_flags": sum("cutoff" in f for f in flags.values()),
        "duplicate_flags": sum("duplicate" in f for f in flags.values()),
        "forward_dated_flags": sum("forward_dated" in f for f in flags.values()),
        "commitments": {str(k): c.root.hex() for k, c in sorted(led.commits.items())},
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        with Path(args.out).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["entry_id", "kind", "date", "record_date", "amount", "counterparty", "doc_id", "flags"])
            for i in sorted(flags):
                r = led.records[i]
                w.writerow(
                    [
                        r["entry_id"],
                        r["kind"],
                        to_date(start, r["eff_day"]),
                        to_date(start, r["rec_day"]),
                        f"{r['amount']:.2f}",
                        r["counterparty"] or "",
                        r["doc_id"] or "",
                        ";".join(flags[i]),
                    ]
                )
    if args.ledger:
        led.save(args.ledger)
    return 0


def _csv_dates(path):
    import csv

    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return [r["date"][:10] for r in csv.DictReader(f)]


def cmd_saft_export(args) -> int:
    from .formats import load_economy, write_saft

    econ = load_economy(args.folder)
    entries = econ.apparent_entries()
    write_saft(entries, args.out, econ.start_date, company=econ.firm, period_bounds=econ.period_bounds)
    n = sum(1 for e in entries if econ.in_year(e.eff_day))
    print(f"{n} transactions of the fiscal year -> {args.out}")
    return 0


def cmd_psi_bench(args) -> int:
    from .psi import have_x25519, intersect

    backends = ["plain", "x25519", "modp"] if args.backend == "all" else [args.backend]
    if "x25519" in backends and not have_x25519():
        backends.remove("x25519")
    a = [f"item-{i}".encode() for i in range(args.n)]
    b = [f"item-{i}".encode() for i in range(args.n // 2, args.n + args.n // 2)]
    rows = []
    for be in backends:
        n = min(args.n, 500) if be == "modp" else args.n
        member, st = intersect(a[:n], b[:n], be)
        rows.append(
            {
                "backend": be,
                "n": n,
                "matched": sum(member),
                "seconds": round(st.seconds, 4),
                "ms_per_item": round(1000 * st.seconds / n, 4),
                "kilobytes_sent": round(st.bytes_sent / 1024, 1),
            }
        )
    print(json.dumps(rows, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="veriledger", description=__doc__)
    p.add_argument("--version", action="version", version=f"veriledger {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("run", help="run a scenario file and write results, figures and an HTML report")
    s.add_argument("config")
    s.add_argument("--out", default="veriledger-output")
    s.add_argument("--replications", type=int, default=None, help="override the number of replications")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("demo", help="run the bundled example scenarios")
    s.add_argument("--out", default="veriledger-demo")
    s.add_argument("--replications", type=int, default=None)
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("simulate", help="generate one economy and write CSV files and a ledger")
    s.add_argument("--config", default=None, help="YAML with 'economy' and 'misstatements' sections")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--out", default="veriledger-economy")
    s.add_argument("--no-truth", action="store_true", help="do not write ground-truth labels")
    s.add_argument("--scenario", default=None, help="write the economy of one replication of this scenario file")
    s.add_argument("--replication", type=int, default=0, help="replication number (with --scenario)")
    s.add_argument("--sweep-value", default=None, help="value of the scenario's sweep parameter (with --scenario)")
    s.set_defaults(func=cmd_simulate)

    s = sub.add_parser("verify", help="check a saved ledger against its commitments")
    s.add_argument("ledger")
    s.add_argument("--commits", default=None, help="independently held commitments file")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("saft-import", help="convert SAF-T Financial general-ledger entries to CSV")
    s.add_argument("file")
    s.add_argument("--out", default="entries.csv")
    s.add_argument("--start", default=None, help="first day of the fiscal year (YYYY-MM-DD)")
    s.set_defaults(func=cmd_saft_import)

    s = sub.add_parser(
        "check", help="record a SAF-T or general-ledger CSV file on a ledger and flag cut-off, duplicates, dates"
    )
    s.add_argument("file", help="SAF-T Financial XML or general-ledger CSV (see docs/formats.md)")
    s.add_argument("--start", default=None, help="first day of the fiscal year (default: from the SAF-T header)")
    s.add_argument("--end", default=None, help="last day of the fiscal year (default: from the header, or 12 months)")
    s.add_argument("--periods", type=int, default=4, help="periods per fiscal year (default 4)")
    s.add_argument("--close-lag", type=int, default=5, help="days after a period end until it is committed")
    s.add_argument("--out", default=None, help="CSV file listing every flagged entry")
    s.add_argument("--ledger", default=None, help="also save the ledger (JSON Lines) to this path")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("saft-export", help="write a minimal SAF-T Financial 1.40 file from an economy folder")
    s.add_argument("folder")
    s.add_argument("--out", default="saft.xml")
    s.set_defaults(func=cmd_saft_export)

    s = sub.add_parser("psi-bench", help="time the private set intersection backends")
    s.add_argument("--n", type=int, default=2000)
    s.add_argument("--backend", default="all", choices=["all", "plain", "x25519", "modp"])
    s.set_defaults(func=cmd_psi_bench)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
