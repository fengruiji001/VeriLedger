"""Independent optional QA; SciPy is not a VeriLedger runtime dependency."""
import math

from scipy.special import gammaincinv

from veriledger.audit import poisson_upper

largest = 0.0
count = 0
for k in (1, 3, 10, 100, 750, 1000, 6000, 10000, 100000, 1000000):
    for confidence in (1e-10, 0.05, 0.1, 0.5, 0.95, 1 - 2**-53):
        actual = poisson_upper(k, confidence)
        reference = float(gammaincinv(k + 1, confidence))
        error = abs(actual - reference) / reference
        assert math.isclose(actual, reference, rel_tol=1e-10, abs_tol=1e-12), (k, confidence, actual, reference)
        largest = max(largest, error)
        count += 1
print(f"{count} Poisson limits matched SciPy gammaincinv; maximum relative error {largest:.3g}")
