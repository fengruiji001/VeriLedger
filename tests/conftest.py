import numpy as np
import pytest

from veriledger.misstate import MisstatementSpec, inject
from veriledger.synth import EconomyConfig, generate

SMALL = dict(
    days=120, periods=4, n_customers=20, n_suppliers=10, sales_per_day=4.0, purchases_per_day=1.5, expenses_per_day=0.5
)
ALL_SCHEMES = dict(backdate=0.3, fictitious=0.02, inflate=0.02, duplicate=0.02, round_trip=3, tamper=0.02)


@pytest.fixture
def small_cfg():
    return EconomyConfig(**SMALL)


@pytest.fixture
def econ(small_cfg):
    return generate(small_cfg, seed=11)


@pytest.fixture
def misstated(econ):
    return inject(econ, MisstatementSpec(**ALL_SCHEMES), seed=12)


@pytest.fixture
def rng():
    return np.random.default_rng(123)
