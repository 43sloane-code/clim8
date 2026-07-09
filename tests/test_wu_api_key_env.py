"""KAT for the env-first WU_API_KEY (weather_council/sources.py, Phase 6c).

WU_API_KEY is the settlement spine's single point of failure (CLAUDE.md HARD RULE 7). It is now
read env-first so the key can be rotated without a code change; the literal is only the default
when WU_API_KEY is unset. This pins that contract: env overrides, absence falls back to the
working default. Network-free (reloads the module under a patched environment).

Run with:  PYTHONPATH=. python3 -m unittest tests.test_wu_api_key_env -v
"""
from __future__ import annotations

import importlib
import os
import unittest

import weather_council.sources as sources


class TestWuApiKeyEnv(unittest.TestCase):
    def tearDown(self):
        # Always restore the module to its unset-env state so other tests see the default.
        os.environ.pop("WU_API_KEY", None)
        importlib.reload(sources)

    def test_env_overrides_the_literal(self):
        os.environ["WU_API_KEY"] = "ROTATED_KEY_123"
        importlib.reload(sources)
        self.assertEqual(sources.WU_API_KEY, "ROTATED_KEY_123")

    def test_falls_back_to_working_default_when_unset(self):
        os.environ.pop("WU_API_KEY", None)
        importlib.reload(sources)
        # A non-empty default must remain so a fresh checkout works with no env set.
        self.assertTrue(sources.WU_API_KEY)
        self.assertEqual(sources.WU_API_KEY, "e1f10a1e78da46f5b10a1e78da96f525")


if __name__ == "__main__":
    unittest.main()
