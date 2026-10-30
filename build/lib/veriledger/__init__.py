"""VeriLedger: simulate blockchain-based accounting verification and measure information asymmetry.

Typical use::

    from veriledger import EconomyConfig, generate, inject, SamplingAudit, CrossLedgerAudit, evaluate

    econ = inject(generate(EconomyConfig(adoption=0.5), seed=1), {"fictitious": 0.01}, seed=2)
    print(evaluate(econ, CrossLedgerAudit().run(econ, rng=3)))
"""

__version__ = "0.3.10"

from .audit import CostModel, CrossLedgerAudit, DetectionModel, LedgerAudit, SamplingAudit, build_ledger
from .disclosure import attested, periodic, public
from .ledger import Ledger
from .lending import lend
from .metrics import evaluate
from .misstate import MisstatementSpec, inject
from .scenario import Scenario, run, summarize
from .synth import EconomyConfig, generate

__all__ = [
    "__version__",
    "CostModel",
    "CrossLedgerAudit",
    "DetectionModel",
    "EconomyConfig",
    "Ledger",
    "LedgerAudit",
    "MisstatementSpec",
    "SamplingAudit",
    "Scenario",
    "attested",
    "build_ledger",
    "evaluate",
    "generate",
    "inject",
    "lend",
    "periodic",
    "public",
    "run",
    "summarize",
]
