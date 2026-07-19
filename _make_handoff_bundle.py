#!/usr/bin/env python3
"""Concatenate every tracked source file into one copy-paste handoff bundle.
Ordered: core software first, then tools, then tests — so the runnable system
is at the top and nothing is omitted below it.

The bundle redacts the literal WU_API_KEY default (the public web key carried
in sources.py) and carries a SHA-256 manifest so a truncated or corrupted paste
can be detected before it is trusted.
"""
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "HANDOFF_CODE_BUNDLE.txt"

# The public web key default embedded in sources.py. Redacted from the handoff
# bundle so a copy-pasted bundle cannot be run without setting WU_API_KEY.
_WU_API_KEY_LITERAL = "e1f10a1e78da46f5b10a1e78da96f525"

# Match the env-fallback assignment that carries the literal public web key.
_WU_KEY_RE = re.compile(
    r'^(WU_API_KEY\s*=\s*os\.environ\.get\s*\(\s*["\']WU_API_KEY["\']\s*,\s*)["\'][^"\']+["\']',
    re.MULTILINE,
)
_REDACTED_KEY = r'\1"<redacted-in-bundle>"'

# Unique body delimiters. Built by concatenation so the literal marker text does
# NOT appear in this source file; otherwise the bundled copy of this module
# would make text.find() match the wrong occurrence.
BEGIN_BODY = "# === BEGIN " + "REDACTED BUNDLE BODY" + " === #"
END_BODY = "# === END " + "REDACTED BUNDLE BODY" + " === #"


def _rank(p: str) -> tuple:
    if p.startswith("tests/"):
        return (2, p)
    if p.startswith("tools/"):
        return (1, p)
    return (0, p)          # core: run.py, server.py, weather_council/*, etc.


def _tracked_files(root: Path) -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=root, capture_output=True, text=True
    ).stdout.split("\n")
    return [f for f in tracked if f.strip()]


def _count_lines(path: Path) -> int:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def make_bundle(root: Path | None = None, out_path: Path | None = None) -> str:
    """Regenerate the handoff bundle. Returns the SHA-256 manifest."""
    root = root or ROOT
    out_path = out_path or OUT
    files = _tracked_files(root)
    files.sort(key=_rank)

    present, missing = [], []
    for f in files:
        (present if (root / f).exists() else missing).append(f)
    counts = {f: _count_lines(root / f) for f in present}
    total = sum(counts.values())

    body_parts: list[str] = []
    for f in present:
        hd = "#" * 88
        body_parts.append("\n" + hd + "\n")
        body_parts.append(f"########## FILE: {f}  ({counts[f]} lines)\n")
        body_parts.append(hd + "\n")
        text = (root / f).read_text(encoding="utf-8", errors="replace")
        text = _WU_KEY_RE.sub(_REDACTED_KEY, text)
        text = text.replace(_WU_API_KEY_LITERAL, "<redacted-in-bundle>")
        body_parts.append(text)
        body_parts.append("\n")
    body = "".join(body_parts)
    digest = hashlib.sha256(
        (BEGIN_BODY + "\n" + body + END_BODY).encode("utf-8")
    ).hexdigest()

    bar = "=" * 87
    header_lines = [
        bar,
        " WEATHER-VERDICT — COMPLETE CODE HANDOFF BUNDLE\n",
        f" Files: {len(present)} present / {len(files)} tracked   Total source lines: {total}\n",
        " COPY EVERYTHING: open this file in an editor, Select All (Cmd+A), Copy (Cmd+C).\n",
        " Order: CORE software first, then tools/, then tests/. Nothing omitted.\n",
        f" SHA-256 manifest: {digest}\n",
        " NOTE: the WU_API_KEY literal default is redacted; set WU_API_KEY in env to run.\n",
        bar + "\n\n",
        "TABLE OF CONTENTS (path — lines):\n",
    ]
    section = None
    for f in present:
        sec = _rank(f)[0]
        if sec != section:
            section = sec
            header_lines.append(f"  --- {['CORE SOFTWARE', 'TOOLS', 'TESTS'][sec]} ---\n")
        header_lines.append(f"    {f:<54} {counts[f]:>6}\n")
    if missing:
        header_lines.append("\n  MISSING TRACKED FILES (not on disk; omitted from bundle):\n")
        for f in missing:
            header_lines.append(f"    {f}\n")
    header_lines.append(f"\n  TOTAL: {total} lines across {len(present)} present files\n\n")

    verify_block = (
        f"{bar}\n"
        " VERIFY: the manifest above is the SHA-256 of the text between the\n"
        f" '{BEGIN_BODY}' and '{END_BODY}' markers. Recompute with:\n"
        f"   shasum -a 256 {out_path.name}\n"
        " (The header lines above the begin-marker are NOT included in the digest.)\n"
        f"{bar}\n"
    )

    with open(out_path, "w", encoding="utf-8") as out:
        out.write("".join(header_lines))
        out.write(BEGIN_BODY + "\n")
        out.write(body)
        out.write(END_BODY + "\n")
        out.write(verify_block)

    return digest


if __name__ == "__main__":
    digest = make_bundle()
    print(f"Bundle written: {OUT.name}")
    print(f"Files: {len(_tracked_files(ROOT))}   Bundle SHA-256: {digest}   Size: "
          f"{OUT.stat().st_size // 1024} KB")
