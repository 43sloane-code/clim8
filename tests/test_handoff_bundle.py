"""KATs for the handoff bundle generator: manifest integrity + key redaction.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import hashlib
import re
import tempfile
import unittest
from pathlib import Path

import _make_handoff_bundle as bundle


_LITERAL_KEY = "e1f10a1e78da46f5b10a1e78da96f525"


class TestHandoffBundle(unittest.TestCase):
    def test_bundle_includes_sha256_manifest_and_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "bundle.txt"
            digest = bundle.make_bundle(root=bundle.ROOT, out_path=out)
            text = out.read_text(encoding="utf-8")

        header_match = re.search(r"SHA-256 manifest: ([0-9a-f]{64})", text)
        self.assertIsNotNone(header_match)
        self.assertEqual(header_match.group(1), digest)

        # The digest covers the body between the unique begin/end markers.
        begin = text.find(bundle.BEGIN_BODY)
        end = text.find(bundle.END_BODY)
        self.assertGreater(begin, 0)
        self.assertGreater(end, begin)
        body = text[begin:end + len(bundle.END_BODY)]
        expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self.assertEqual(expected, digest)

    def test_bundle_redacts_wu_api_key_default(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "bundle.txt"
            bundle.make_bundle(root=bundle.ROOT, out_path=out)
            text = out.read_text(encoding="utf-8")

        self.assertNotIn(_LITERAL_KEY, text)
        self.assertIn('WU_API_KEY = os.environ.get("WU_API_KEY", "<redacted-in-bundle>")', text)

    def test_default_out_path_is_repo_root_handoff_bundle(self):
        self.assertEqual(bundle.OUT, bundle.ROOT / "HANDOFF_CODE_BUNDLE.txt")
