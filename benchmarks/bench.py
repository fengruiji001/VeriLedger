"""Runtime benchmarks: ledger construction and verification, cross-ledger matching, PSI backends.

Usage: python benchmarks/bench.py [--out benchmarks/results.json] [--sizes 10000 100000 1000000]
"""

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np

from veriledger import __version__
from veriledger.audit import CrossLedgerAudit, LedgerAudit, build_ledger
from veriledger.misstate import inject
from veriledger.psi import have_x25519, intersect
from veriledger.synth import EconomyConfig, generate


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t


def economy_of_size(n_entries: int, seed: int = 0):
    # about 3.9 ledger entries per sale at the default mix (sale, cost of sales, receipt, purchases, payments)
    per_day = max(1.0, n_entries / 365 / 3.9)
    cfg = EconomyConfig(
        sales_per_day=per_day,
        purchases_per_day=per_day * 0.42,
        expenses_per_day=per_day * 0.17,
        n_customers=200,
        n_suppliers=80,
    )
    return generate(cfg, seed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="benchmarks/results.json")
    ap.add_argument("--sizes", type=int, nargs="+", default=[10_000, 100_000, 1_000_000])
    ap.add_argument("--psi", type=int, nargs="+", default=[1_000, 10_000])
    args = ap.parse_args()
    res = {
        "veriledger": __version__,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "ledger": [],
        "psi": [],
    }
    for n in args.sizes:
        econ, t_gen = timed(lambda n=n: economy_of_size(n))
        econ = inject(econ, {"fictitious": 0.01, "inflate": 0.01, "tamper": 0.001}, seed=1)
        led, t_build = timed(lambda e=econ: build_ledger(e))
        rep, t_verify = timed(led.verify)
        _, t_ledger_audit = timed(lambda e=econ, d=led: LedgerAudit().run(e, ledger=d, rng=0))
        _, t_cross = timed(lambda e=econ, d=led: CrossLedgerAudit(residual_sampling=False).run(e, ledger=d, rng=0))
        row = {
            "entries": len(led.records),
            "generate_s": round(t_gen, 2),
            "build_s": round(t_build, 2),
            "verify_s": round(t_verify, 2),
            "ledger_audit_s": round(t_ledger_audit, 2),
            "crossledger_plain_s": round(t_cross, 2),
            "tampered_found": len(rep.modified),
            "tampered_injected": len(econ.tampering),
        }
        res["ledger"].append(row)
        print(row, flush=True)
    backends = ["plain"] + (["x25519"] if have_x25519() else []) + ["modp"]
    done = set()
    for n in args.psi:
        for be in backends:
            m = min(n, 1000) if be == "modp" else n  # the pure-Python MODP group is timed on 1,000 items
            if (be, m) in done:
                continue
            done.add((be, m))
            a = [f"doc-{i}".encode() for i in range(m)]  # half of each set is in the other
            b = [f"doc-{i}".encode() for i in range(m // 2, m + m // 2)]
            member, st = intersect(a, b, be)
            row = {
                "backend": be,
                "n_each": m,
                "seconds": round(st.seconds, 3),
                "us_per_element": round(1e6 * st.seconds / (2 * m), 1),
                "kb_sent": round(st.bytes_sent / 1024, 1),
                "matched": int(np.sum(member)),
            }
            res["psi"].append(row)
            print(row, flush=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
