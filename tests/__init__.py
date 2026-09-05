"""Offline characterization tests for the current implementation."""

from __future__ import annotations

import sys
import types


# The production module imports requests eagerly.  Keep the regression suite
# runnable in a clean checkout without installing optional runtime packages.
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")

    def _network_disabled(*args, **kwargs):
        raise AssertionError("network access is disabled in characterization tests")

    requests_stub.get = _network_disabled
    sys.modules["requests"] = requests_stub
