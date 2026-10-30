"""Merkle trees with domain-separated hashing and inclusion proofs (RFC 9162, Section 2.1).

Leaves are hashed as ``H(0x00 || data)`` and internal nodes as ``H(0x01 || left || right)``. When a
level has an odd number of nodes, the last node is carried up unchanged rather than duplicated, which
gives the same tree as the RFC 9162 split at the largest power of two and avoids the duplicate-leaf
ambiguity of Bitcoin-style trees. Proofs are verified from the leaf index and the tree size, as in
RFC 9162, so a proof binds the position of the leaf as well as its content.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

LEAF = b"\x00"
NODE = b"\x01"


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def leaf_hash(data: bytes) -> bytes:
    return sha256(LEAF + data)


def node_hash(left: bytes, right: bytes) -> bytes:
    return sha256(NODE + left + right)


EMPTY_ROOT = sha256(b"")


@dataclass(frozen=True)
class Proof:
    """Inclusion proof: the leaf index, the tree size and the sibling hashes from leaf to root."""

    index: int
    size: int
    path: tuple[bytes, ...]

    def to_dict(self) -> dict:
        return {"index": self.index, "size": self.size, "path": [h.hex() for h in self.path]}

    @classmethod
    def from_dict(cls, d: dict) -> Proof:
        # also accepts the 0.1.0 format, in which each step was [hash, sibling_is_left]
        if any(isinstance(d[key], bool) or not isinstance(d[key], int) for key in ("index", "size")):
            raise ValueError("proof index and size must be integers")
        path = tuple(bytes.fromhex(h[0] if isinstance(h, list) else h) for h in d["path"])
        return cls(d["index"], d["size"], path)


class MerkleTree:
    """A static Merkle tree over pre-hashed leaves."""

    def __init__(self, leaves: list[bytes]):
        self.levels: list[list[bytes]] = [list(leaves)]
        while len(self.levels[-1]) > 1:
            cur = self.levels[-1]
            nxt = [node_hash(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
            if len(cur) % 2:
                nxt.append(cur[-1])
            self.levels.append(nxt)

    @property
    def size(self) -> int:
        return len(self.levels[0])

    @property
    def root(self) -> bytes:
        return self.levels[-1][0] if self.size else EMPTY_ROOT

    def proof(self, index: int) -> Proof:
        if not 0 <= index < self.size:
            raise IndexError(index)
        path = []
        i = index
        for level in self.levels[:-1]:
            sib = i ^ 1
            if sib < len(level):
                path.append(level[sib])
            i //= 2
        return Proof(index, self.size, tuple(path))


def verify_proof(leaf: bytes, proof: Proof, root: bytes) -> bool:
    """Check that ``leaf`` (already leaf-hashed) sits at ``proof.index`` of a tree of ``proof.size``
    leaves with root ``root`` (the algorithm of RFC 9162, Section 2.1.3.2)."""
    if not isinstance(proof, Proof) or not isinstance(proof.path, (tuple, list)):
        return False
    if (
        not isinstance(leaf, bytes)
        or len(leaf) != 32
        or not isinstance(root, bytes)
        or len(root) != 32
        or isinstance(proof.index, bool)
        or isinstance(proof.size, bool)
        or not isinstance(proof.index, int)
        or not isinstance(proof.size, int)
        or not 0 <= proof.index < proof.size
        or any(not isinstance(h, bytes) or len(h) != 32 for h in proof.path)
    ):
        return False
    fn, sn, r = proof.index, proof.size - 1, leaf
    for p in proof.path:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            while not fn & 1 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == root


def merkle_root(leaves: list[bytes]) -> bytes:
    return MerkleTree(leaves).root
