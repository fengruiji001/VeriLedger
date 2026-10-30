import json

import numpy as np
import pytest

from veriledger.audit import build_ledger
from veriledger.ledger import Commitment, Ledger
from veriledger.model import Entry, Line, make_entry

BOUNDS = [(0, 9), (10, 19)]


def test_append_rules():
    led = Ledger("F", BOUNDS)
    led.append(make_entry("E1", "sale", 1, 10, rec_day=2))
    with pytest.raises(ValueError):
        led.append(make_entry("E2", "sale", 1, 10, rec_day=1))  # rec_day decreases
    with pytest.raises(ValueError):
        led.append(make_entry("E1", "sale", 3, 10, rec_day=3))  # duplicate id
    with pytest.raises(ValueError):
        led.append(Entry("E3", "sale", 3, 3, [Line("1500", debit=5), Line("3000", credit=4)], 5))
    with pytest.raises(ValueError):
        Ledger("F", [])


def test_commit_and_late_entries():
    led = Ledger("F", BOUNDS, close_lag=2)
    led.append(make_entry("E1", "sale", 5, 10, rec_day=5))
    led.append(make_entry("E2", "sale", 9, 10, rec_day=11))  # posted in the close window: still a member
    led.append(make_entry("E3", "sale", 8, 10, rec_day=13))  # after commit day 11 -> late
    led.close()
    assert led.commits[0].size == 2
    assert led.late == {2}
    with pytest.raises(KeyError):
        led.prove("E3")
    assert led.cutoff_flags() == [1, 2]


def test_inclusion_proofs(misstated):
    led = build_ledger(misstated)
    rng = np.random.default_rng(0)
    nper = len(led.period_bounds)
    ids = [
        r["entry_id"]
        for i, r in enumerate(led.records)
        if i not in led.late and led.period_of(r["eff_day"]) < nper and r["entry_id"] not in misstated.tampering
    ]
    for eid in rng.choice(ids, size=25, replace=False):
        k, proof = led.prove(str(eid))
        rec = led.records[led.index[str(eid)]]
        assert led.verify_inclusion(rec, k, proof, commitment=led.commits[k])
        forged = dict(rec, amount=rec["amount"] + 1)
        assert not led.verify_inclusion(forged, k, proof, commitment=led.commits[k])


def test_clean_ledger_verifies(econ):
    led = build_ledger(econ)
    rep = led.verify()
    assert rep.ok and rep.n_records == len(econ.entries)


def test_tampering_is_detected(misstated):
    led = build_ledger(misstated)
    rep = led.verify()
    tampered = set(misstated.tampering)
    assert tampered and led.entry_ids(rep.modified) == tampered
    assert not rep.ok and rep.bad_periods


@pytest.mark.parametrize("seed", range(10))
def test_random_modifications_always_detected(econ, seed):
    rng = np.random.default_rng(seed)
    led = build_ledger(econ)
    i = int(rng.integers(len(led.records)))
    field = str(rng.choice(["amount", "eff_day", "counterparty", "doc_id", "description"]))
    rec = led.records[i]
    new = {
        "amount": rec["amount"] + 0.01,
        "eff_day": rec["eff_day"] + 1,
        "counterparty": "X",
        "doc_id": "X",
        "description": "changed",
    }[field]
    led.tamper(rec["entry_id"], **{field: new})
    rep = led.verify()
    assert rep.modified == [i] and not rep.ok


def test_duplicate_flags():
    led = Ledger("F", BOUNDS)
    led.append(make_entry("E1", "sale", 1, 10, doc_id="D1"))
    led.append(make_entry("E2", "sale", 2, 10, doc_id="D1"))
    led.append(make_entry("E3", "purchase", 3, 10, doc_id="D1"))  # other kind: not a duplicate
    assert led.duplicate_flags() == [1]


def test_save_load_roundtrip(tmp_path, misstated):
    led = build_ledger(misstated)
    p = tmp_path / "ledger.jsonl"
    led.save(p)
    back = Ledger.load(p)
    assert len(back.records) == len(led.records)
    assert back.chain == led.chain and back.late == led.late
    assert {k: c.root for k, c in back.commits.items()} == {k: c.root for k, c in led.commits.items()}
    assert led.entry_ids(back.verify().modified) == set(misstated.tampering)


def test_rewritten_file_detected_by_external_commitments(tmp_path, econ):
    led = build_ledger(econ)
    published = dict(led.commits)  # copies held by outsiders
    # an attacker edits one record and rebuilds every hash and commitment consistently
    entries = [e.copy() for e in econ.entries]
    target = sorted(entries, key=lambda e: (e.rec_day, e.entry_id))[5]
    target.amount += 100
    target.lines = [
        Line(ln.account, target.amount if ln.debit else 0, target.amount if ln.credit else 0) for ln in target.lines
    ]
    forged = Ledger.from_entries(econ.firm, entries, econ.period_bounds)
    p = tmp_path / "ledger.jsonl"
    forged.save(p)
    back = Ledger.load(p)
    assert back.verify().ok  # internally consistent ...
    assert not back.verify(published).ok  # ... but not with the commitments published earlier
    assert json.loads((tmp_path / "ledger.jsonl.commits.json").read_text())[0]["period"] == 0
    assert isinstance(Commitment.from_dict(published[0].to_dict()), Commitment)


def _rewrite_without(path, out, drop, genesis):
    """Delete records from a saved ledger and recompute its stored hashes consistently."""
    import json

    from veriledger.ledger import canonical
    from veriledger.merkle import leaf_hash, sha256

    lines = path.read_text().splitlines()
    recs = [json.loads(x) for i, x in enumerate(lines[1:]) if i not in drop]
    prev, rows = genesis, [lines[0]]
    for d in recs:
        lf = leaf_hash(canonical(d["record"]))
        prev = sha256(prev + lf)
        d["leaf"], d["chain"] = lf.hex(), prev.hex()
        rows.append(json.dumps(d))
    out.write_text("\n".join(rows) + "\n")


@pytest.mark.parametrize("where", ["end", "middle"])
def test_deleted_records_fail_verification(tmp_path, econ, where):
    led = build_ledger(econ)
    led.save(tmp_path / "l.jsonl")
    external = {k: c for k, c in led.commits.items()}
    n, committed = len(led.records), max(c.n_records for c in led.commits.values())
    assert committed == n  # the closing checkpoint covers every record, including the following year
    drop = set(range(n - 5, n)) if where == "end" else {10}
    _rewrite_without(tmp_path / "l.jsonl", tmp_path / "d.jsonl", drop, led.genesis)
    loaded = Ledger.load(tmp_path / "d.jsonl")
    rep = loaded.verify(external)
    assert not rep.ok and rep.bad_periods
    if where == "end":
        assert rep.missing_records == 5


def test_cli_verify_reports_deletion(tmp_path, econ, capsys):
    import json

    from veriledger.cli import main

    led = build_ledger(econ)
    led.save(tmp_path / "l.jsonl")
    (tmp_path / "ext.json").write_text((tmp_path / "l.jsonl.commits.json").read_text())
    n, committed = len(led.records), max(c.n_records for c in led.commits.values())
    _rewrite_without(tmp_path / "l.jsonl", tmp_path / "d.jsonl", set(range(committed - 3, n)), led.genesis)
    assert main(["verify", str(tmp_path / "d.jsonl"), "--commits", str(tmp_path / "ext.json")]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["missing_records"] == 3 and out["external_commitments_supplied"] and not out["ok"]
    assert out["independent_commitments"] is None and not out["trust_verified"]


def test_cli_verify_warns_without_independent_commitments(tmp_path, econ, capsys):
    from veriledger.cli import main

    build_ledger(econ).save(tmp_path / "l.jsonl")
    assert main(["verify", str(tmp_path / "l.jsonl")]) == 0
    assert "no independent commitments" in capsys.readouterr().err


def test_inclusion_proof_requires_committed_size(econ):
    from veriledger.merkle import Proof

    led = build_ledger(econ)
    eid = led.records[5]["entry_id"]
    k, p = led.prove(eid)
    rec = led.records[led.index[eid]]
    assert led.verify_inclusion(rec, k, p, commitment=led.commits[k])
    assert not led.verify_inclusion(rec, k, Proof(p.index, p.size + 1, p.path), commitment=led.commits[k])


def test_forward_dated_flags():
    from veriledger.model import make_entry

    led = Ledger.from_entries("F", [make_entry("A", "sale", 10, 5.0, rec_day=8)], [(0, 89)])
    assert led.forward_flags() == [0]


def test_closing_checkpoint_covers_following_year(econ):
    led = build_ledger(econ, tampered=False)
    k = len(econ.period_bounds)
    after = [i for i, r in enumerate(led.records) if r["eff_day"] > econ.fiscal_end and i not in led.late]
    assert after and k in led.commits and led.commits[k].n_records == len(led.records)
    rec = led.records[after[0]]
    p, proof = led.prove(rec["entry_id"])
    assert p == k and led.verify_inclusion(rec, k, proof, commitment=led.commits[k])
    assert led.checkpoint() is None  # nothing left uncovered
