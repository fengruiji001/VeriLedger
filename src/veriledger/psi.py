"""Two-party private set intersection (PSI) based on commutative blinding (Diffie-Hellman PSI).

Party A holds set X and party B holds set Y. Each party hashes its items into a group and raises them
to a secret exponent. A sends ``H(x)^a`` to B, B returns ``H(x)^{ab}`` together with ``H(y)^b``, and A
computes ``H(y)^{ba}``. This is an in-process matching simulation: both inputs are present in the
same process, and no confidentiality or resistance to malicious peers has been established.
Three backends are provided:

* ``x25519``: Montgomery-ladder scalar multiplication on Curve25519 via the ``cryptography`` package;
  fast; chosen by ``get_backend("auto")`` when available. Items are hashed directly to a u-coordinate
  (SHA-256), not with a standard hash-to-curve map (RFC 9380); this is adequate for simulation.
* ``modp``: pure-Python modular exponentiation in the 2048-bit MODP group of RFC 3526 (quadratic-residue
  subgroup); slow, dependency-free reference implementation.
* ``plain``: keyed hashing with a key shared by both parties. It gives the same intersection and is
  meant only for large simulations; it offers no privacy against the counterparty. The audit regimes
  use it by default (``CrossLedgerAudit(psi_backend="plain")``) because their results do not depend on
  the backend.

In an audit the auditor plays party A with the firm's ledger records as they stand, so the matching
result goes to the auditor and not to the audited firm.

This module is for research simulation. It has not been audited and must not be used to protect real
data.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass

# RFC 3526, 2048-bit MODP group (group 14); generator 2, safe prime p = 2q + 1
MODP_P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
)
MODP_Q = (MODP_P - 1) // 2
DOMAIN = b"veriledger-psi-v1"


def have_x25519() -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: F401
    except Exception:  # pragma: no cover - depends on the environment
        return False
    return True


class Backend:
    name = "base"
    element_bytes = 32

    def new_key(self):
        raise NotImplementedError

    def hash_to_group(self, item: bytes):
        raise NotImplementedError

    def blind(self, key, element):
        raise NotImplementedError


class X25519Backend(Backend):
    name = "x25519"
    element_bytes = 32

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

        self._priv = X25519PrivateKey
        self._pub = X25519PublicKey

    def new_key(self):
        return self._priv.from_private_bytes(os.urandom(32))

    def hash_to_group(self, item: bytes) -> bytes:
        return hashlib.sha256(DOMAIN + b"|x25519|" + item).digest()

    def blind(self, key, element: bytes) -> bytes:
        try:
            return key.exchange(self._pub.from_public_bytes(element))
        except ValueError:  # low-order input (probability ~2^-250 for hashed inputs)
            return key.exchange(self._pub.from_public_bytes(hashlib.sha256(b"retry" + element).digest()))


class ModpBackend(Backend):
    name = "modp"
    element_bytes = 256

    def new_key(self) -> int:
        while True:
            k = int.from_bytes(os.urandom(32), "big") % MODP_Q
            if k > 1:
                return k

    def hash_to_group(self, item: bytes) -> int:
        h = b"".join(hashlib.sha256(DOMAIN + b"|modp|" + bytes([i]) + item).digest() for i in range(8))
        x = int.from_bytes(h, "big") % MODP_P
        return pow(x, 2, MODP_P)  # map into the prime-order subgroup of quadratic residues

    def blind(self, key: int, element: int) -> int:
        return pow(element, key, MODP_P)


class PlainBackend(Backend):
    """Keyed hashing with a shared key: same result, no privacy (simulation speed only)."""

    name = "plain"
    element_bytes = 32

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or os.urandom(32)

    def new_key(self):
        return None

    def hash_to_group(self, item: bytes) -> bytes:
        return hmac.new(self._key, item, hashlib.sha256).digest()

    def blind(self, key, element):
        return element


def get_backend(name: str = "auto") -> Backend:
    if name == "auto":
        name = "x25519" if have_x25519() else "modp"
    if name == "x25519":
        return X25519Backend()
    if name == "modp":
        return ModpBackend()
    if name == "plain":
        return PlainBackend()
    raise ValueError(f"unknown PSI backend {name!r}; use 'x25519', 'modp', 'plain' or 'auto'")


@dataclass
class PSIStats:
    backend: str
    n_a: int
    n_b: int
    group_ops: int
    bytes_sent: int
    seconds: float


def intersect(
    items_a: list[bytes], items_b: list[bytes], backend: str | Backend = "auto"
) -> tuple[list[bool], PSIStats]:
    """Run the PSI protocol between A and B in-process.

    Returns, for each of A's items, whether it is in B's set, plus protocol statistics.
    """
    be = backend if isinstance(backend, Backend) else get_backend(backend)
    t0 = time.perf_counter()
    ka, kb = be.new_key(), be.new_key()
    # A -> B: H(x)^a
    a1 = [be.blind(ka, be.hash_to_group(x)) for x in items_a]
    # B -> A: H(x)^ab (same order) and H(y)^b
    a2 = [be.blind(kb, v) for v in a1]
    b1 = [be.blind(kb, be.hash_to_group(y)) for y in items_b]
    # A: H(y)^ba
    b2 = {_key(be.blind(ka, v)) for v in b1}
    member = [_key(v) in b2 for v in a2]
    ops = 0 if be.name == "plain" else 2 * len(items_a) + 2 * len(items_b)
    sent = be.element_bytes * (2 * len(items_a) + len(items_b))
    return member, PSIStats(be.name, len(items_a), len(items_b), ops, sent, time.perf_counter() - t0)


def _key(v) -> bytes:
    if isinstance(v, int):
        return v.to_bytes(256, "big")
    return bytes(v)
