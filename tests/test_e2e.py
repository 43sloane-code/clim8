"""End-to-end scenario gate: happy path, error case, edge case.

These are the three scenarios the black-box harness (tools/verify.py) narrates in
plain English. We import that harness's scenario functions here so the SAME checks
also ride the network-free regression gate (`make check`) — one source of truth,
no drift between the human-readable report and the CI gate. Each scenario drives
the real shipped code (compare_high, run._market_lines, the security sandbox);
nothing is mocked.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest

# Load tools/verify.py by path (tools/ is a script dir, not an importable package).
# Register in sys.modules BEFORE exec so the @dataclass in the harness can resolve
# its own __module__ (dataclasses looks the module up in sys.modules at class build).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "verify_harness", os.path.join(_ROOT, "tools", "verify.py"))
verify = importlib.util.module_from_spec(_spec)
sys.modules["verify_harness"] = verify
_spec.loader.exec_module(verify)


class TestEndToEndScenarios(unittest.TestCase):
    def test_happy_whole_degree_comparison_surfaces(self):
        r = verify.scenario_happy()
        self.assertTrue(r.ok, "HAPPY scenario failed:\n" + "\n".join(r.evidence))

    def test_error_sandbox_fails_closed(self):
        r = verify.scenario_error()
        self.assertTrue(r.ok, "ERROR scenario failed:\n" + "\n".join(r.evidence))

    def test_edge_sub_degree_same_station(self):
        r = verify.scenario_edge()
        self.assertTrue(r.ok, "EDGE scenario failed:\n" + "\n".join(r.evidence))

    def test_honesty_invariants_hold(self):
        for r in verify.invariants():
            self.assertTrue(r.ok, f"invariant failed ({r.title}):\n"
                            + "\n".join(r.evidence))


if __name__ == "__main__":
    unittest.main()
