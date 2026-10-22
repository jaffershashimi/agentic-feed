"""Fails if the public repository contains account-like data, private origins or secrets.

This is a regression guard, not proof of privacy. Review every change by hand.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
TEXT = {".py", ".md", ".json", ".yml", ".yaml", ".txt", ".toml", ".csv", ".cfg", ".ini", ""}
PATTERNS = {
    "private production origin": re.compile(r"[a-z0-9-]+\.[a-z0-9-]+\.chatgpt\.site", re.I),
    "scheduler identifier": re.compile(r"\btrig_[A-Za-z0-9]{12,}"),
    "literal bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "brokerage account number": re.compile(r"\b(?:account|acct)[^\n]{0,20}\b\d{9,12}\b", re.I),
    "masked account reference": re.compile(r"[•*x]{2,}\d{4}\b"),
    "account balance or position field": re.compile(r"\"(?:total_value|buying_power|positions?|balance|cash_balance|equity_value)\"\s*:", re.I),
}
ALLOWED = {Path("scripts/privacy_check.py"), Path("tests/test_privacy.py")}


def scan(root: Path = ROOT) -> list[str]:
    problems = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_dir() or any(part in SKIP_DIRS for part in rel.parts) or rel in ALLOWED:
            continue
        if rel.name.startswith(".env") and rel.name != ".env.example":
            problems.append(f"{rel}: environment file must not be committed")
            continue
        if path.suffix not in TEXT:
            continue
        text = path.read_text(errors="ignore")
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{rel}: {label}")
    return problems


def main() -> int:
    problems = scan()
    for problem in problems:
        print("PRIVACY:", problem)
    if problems:
        return 1
    print("Privacy check passed: no account data, private origins or secrets detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
