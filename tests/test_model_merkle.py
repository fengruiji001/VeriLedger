import json

import numpy as np
import pytest

from veriledger.merkle import EMPTY_ROOT, MerkleTree, Proof, leaf_hash, merkle_root, node_hash, verify_proof
from veriledger.model import Entry, Line, make_entry, with_amount


def test_line_validation():
    with pytest.raises(ValueError):
        Line("1500", debit=-1)
    with pytest.raises(ValueError):
        Line("1500", debit=1, credit=1)


@pytest.mark.parametrize("kind", ["sale", "purchase", "cogs", "receipt", "payment", "expense"])
def test_make_entry_is_balanced(kind):
    e = make_entry("E1", kind, 3, 123.456, counterparty="C1", doc_id="D1")
    assert e.is_balanced()
    assert e.amount == 123.46
    assert e.true_amount == e.amount and e.true_day == e.eff_day


def test_unbalanced_entry_detected():
    e = Entry("E1", "sale", 0, 0, [Line("1500", debit=10), Line("3000", credit=9)], 10)
    assert not e.is_balanced()


def test_canonical_is_deterministic_and_excludes_truth():
    e = make_entry("E1", "sale", 3, 100, doc_id="D1")
    e2 = e.copy(misstatement="inflate", true_amount=50.0)
    assert e.canonical() == e2.canonical()
    rec = json.loads(e.canonical())
    assert "true_amount" not in rec and "misstatement" not in rec


def test_with_amount_keeps_balance_and_truth():
    e = make_entry("E1", "sale", 3, 100)
    f = with_amount(e, 250)
    assert f.is_balanced() and f.amount == 250 and f.true_amount == 100
    assert e.amount == 100  # original unchanged


def test_economy_copy_is_independent(econ):
    c = econ.copy()
    c.entries[0].misstatement = "x"
    c.tampering["E"] = 1.0
    assert econ.entries[0].misstatement is None and not econ.tampering


def test_apparent_entries_apply_tampering(econ):
    c = econ.copy()
    eid = c.entries[0].entry_id
    c.tampering[eid] = 999.0
    app = {e.entry_id: e for e in c.apparent_entries()}
    assert app[eid].amount == 999.0 and app[eid].is_balanced()
    assert c.entries[0].amount != 999.0


def test_empty_tree_root():
    assert MerkleTree([]).root == EMPTY_ROOT


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 33])
def test_all_proofs_verify(n):
    leaves = [leaf_hash(f"x{i}".encode()) for i in range(n)]
    t = MerkleTree(leaves)
    for i in range(n):
        p = t.proof(i)
        assert verify_proof(leaves[i], p, t.root)
        assert not verify_proof(leaf_hash(b"other"), p, t.root)
        assert Proof.from_dict(p.to_dict()) == p


def test_single_leaf_root_is_leaf():
    leaf = leaf_hash(b"a")
    assert merkle_root([leaf]) == leaf


def test_root_changes_with_any_leaf():
    rng = np.random.default_rng(0)
    for n in (2, 5, 12):
        leaves = [leaf_hash(bytes([i])) for i in range(n)]
        r = merkle_root(leaves)
        j = int(rng.integers(n))
        mod = list(leaves)
        mod[j] = leaf_hash(b"changed")
        assert merkle_root(mod) != r


def test_domain_separation():
    a, b = leaf_hash(b"a"), leaf_hash(b"b")
    # an internal node cannot be presented as a leaf of the same data
    assert node_hash(a, b) != leaf_hash(a + b)


def test_proof_index_errors():
    t = MerkleTree([leaf_hash(b"a")])
    with pytest.raises(IndexError):
        t.proof(1)


def test_tampered_path_fails():
    leaves = [leaf_hash(bytes([i])) for i in range(6)]
    t = MerkleTree(leaves)
    p = t.proof(2)
    bad = Proof(p.index, p.size, (leaf_hash(b"z"),) + p.path[1:])
    assert not verify_proof(leaves[2], bad, t.root)


@pytest.mark.parametrize("n", [2, 3, 5, 6, 7, 12, 33])
def test_proof_binds_index_and_size(n):
    leaves = [leaf_hash(f"x{i}".encode()) for i in range(n)]
    t = MerkleTree(leaves)
    for i in range(n):
        p = t.proof(i)
        for j in range(n):
            if j != i:
                assert not verify_proof(leaves[i], Proof(j, p.size, p.path), t.root)
        assert not verify_proof(leaves[i], Proof(i, p.size, p.path + (leaves[0],)), t.root)
        assert not verify_proof(leaves[i], Proof(i, p.size, p.path[:-1]), t.root)


def test_internal_node_is_not_a_leaf():
    leaves = [leaf_hash(bytes([i])) for i in range(8)]
    t = MerkleTree(leaves)
    node = t.levels[1][0]
    shortened = Proof(0, t.size, t.proof(0).path[1:])
    assert not verify_proof(node, shortened, t.root)


def test_old_proof_format_is_read():
    t = MerkleTree([leaf_hash(bytes([i])) for i in range(5)])
    p = t.proof(3)
    old = {"index": 3, "size": 5, "path": [[h.hex(), True] for h in p.path]}
    assert Proof.from_dict(old) == p
