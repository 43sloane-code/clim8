#!/usr/bin/env python3
"""Regenerate TREEVIEW.md from the live repo working tree.

Doc-only automation: walks the repo, renders the standard tree listing, and
rewrites TREEVIEW.md with a fresh generation date and recomputed size context.
Never touches code, ledgers, or served numbers.

Exclusions (non-content artifacts only): .git/, __pycache__/, .ruff_cache/,
.cache/, .harness_opt/, .DS_Store, *.pyc.
"""

import os
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "TREEVIEW.md"

EXCLUDE_DIRS = {".git", "__pycache__", ".ruff_cache", ".cache", ".harness_opt"}
EXCLUDE_FILES = {".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc"}


def _visible_entries(dirpath: Path):
    """Return (dirs, files) of included entries, ASCII-sorted, dirs first."""
    dirs, files = [], []
    for entry in dirpath.iterdir():
        name = entry.name
        if entry.is_dir() and not entry.is_symlink():
            if name not in EXCLUDE_DIRS:
                dirs.append(name)
        else:
            if name in EXCLUDE_FILES or entry.suffix in EXCLUDE_SUFFIXES:
                continue
            files.append(name)
    return sorted(dirs), sorted(files)


def _walk(dirpath: Path, prefix: str, lines: list):
    dirs, files = _visible_entries(dirpath)
    entries = [(d, True) for d in dirs] + [(f, False) for f in files]
    for i, (name, is_dir) in enumerate(entries):
        last = i == len(entries) - 1
        connector = "└── " if last else "├── "
        lines.append(f"{prefix}{connector}{name}{'/' if is_dir else ''}")
        if is_dir:
            extension = "    " if last else "│   "
            _walk(dirpath / name, prefix + extension, lines)


def _du_kb(path: Path) -> int:
    """Disk usage in KB, matching `du -sk` (block-rounded, like the original header)."""
    out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, check=True)
    return int(out.stdout.split()[0])


def _fmt_size(kb: int) -> str:
    if kb >= 1024:
        return f"{round(kb / 1024)} MB"
    return f"{round(kb / 10) * 10} KB"


def main() -> int:
    lines = ["weather-verdict/"]
    _walk(REPO, "", lines)

    n_entries = len(lines)  # tree lines + root line
    n_tests = sum(1 for p in (REPO / "tests").glob("test_*.py"))
    # test count = what the pre-commit gate actually runs (make test:
    # `python3 -m unittest discover -s tests`), not a text grep
    sys.path.insert(0, str(REPO))  # test modules import weather_council
    n_test_cases = unittest.TestLoader().discover(str(REPO / "tests")).countTestCases()
    # report entries = files under reports/ (recursive, streams/ included)
    n_report_entries = sum(
        1
        for root, dirnames, filenames in os.walk(REPO / "reports")
        for name in filenames
        if name not in EXCLUDE_FILES and not name.endswith(".pyc")
    )

    db_size = _fmt_size(_du_kb(REPO / "verdicts.db"))
    data_size = _fmt_size(_du_kb(REPO / "data"))
    reports_size = _fmt_size(_du_kb(REPO / "reports"))
    ledger_size = _fmt_size(_du_kb(REPO / "ledger"))

    header = f"""# TREEVIEW — weather-verdict

Complete directory tree of the live repo (`Desktop/mock projects/weather-verdict`),
generated {date.today().isoformat()}. **Nothing is omitted** from the listing itself; the only
exclusions are non-content artifacts: `.git/`, `__pycache__/`, `.ruff_cache/`,
`.cache/`, `.harness_opt/`, `.DS_Store`, and `*.pyc`.

Size context: `verdicts.db` ≈ {db_size} · `data/` ≈ {data_size} · `reports/` ≈ {reports_size} ·
`ledger/` ≈ {ledger_size} · {n_tests} test files (~{round(n_test_cases / 10) * 10} tests) · {n_report_entries} report entries ·
{n_entries} entries listed below.

```
"""
    OUT.write_text(header + "\n".join(lines) + "\n```\n")
    print(f"TREEVIEW.md regenerated: {n_entries} entries, {n_tests} test files, "
          f"{n_report_entries} report entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
