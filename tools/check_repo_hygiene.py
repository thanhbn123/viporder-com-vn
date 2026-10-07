#!/usr/bin/env python3
"""Repository hygiene gate for VIPORDER.COM.VN.

Fails the build when the repository contains something that must never be
committed:

* environment files / credentials
* runtime databases and lead data
* build artefacts and logs
* files large enough to indicate an accidental binary/data drop
* secret-shaped strings in tracked text

Rationale: this repository is PUBLIC. A leaked production credential or a
customer lead file is unrecoverable once pushed — history rewrites do not
un-publish anything that was already scraped. Better to fail loudly here.

Run:  python tools/check_repo_hygiene.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MiB

# --- Paths that must never be tracked ---------------------------------------
# Note the .env.example exception: a template with EMPTY values is expected and
# useful; a real .env is not.
FORBIDDEN_PATH_PATTERNS: list[tuple[str, str]] = [
    (r"(^|/)\.env$", "environment file with real values"),
    (r"(^|/)\.env\.(?!example$)[^/]+$", "environment file with real values"),
    (r"\.(db|sqlite|sqlite3)$", "runtime database"),
    (r"\.db-(wal|shm)$", "runtime database sidecar"),
    (r"(^|/)var/", "runtime data directory"),
    (r"(^|/)node_modules/", "vendored dependencies"),
    (r"(^|/)\.venv/", "virtualenv"),
    (r"(^|/)__pycache__/", "python bytecode cache"),
    (r"\.pyc$", "python bytecode"),
    (r"\.log$", "log file"),
    (r"\.pem$", "private key material"),
    (r"\.p12$", "private key material"),
    (r"\.key$", "private key material"),
    (r"(^|/)leads?[-_]?\d", "lead data export"),
    (r"\.(xlsx|xls|csv)$", "possible customer data export"),
    (r"(^|/)\.DS_Store$", "macOS metadata"),
]

# --- Secret-shaped content --------------------------------------------------
# These are deliberately narrow so false positives stay near zero. Anything
# matched here is worth a human look.
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key block"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", "GitHub token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bsk-[A-Za-z0-9]{32,}\b", "OpenAI-style API key"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "Slack token"),
    (r"\bAIza[0-9A-Za-z_\-]{35}\b", "Google API key"),
    (r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b", "JWT"),
    (r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*[\"'][^\"']{16,}[\"']",
     "hard-coded credential"),
]

# A missing-value template is fine: KEY="" or KEY=changeme
PLACEHOLDER_VALUES = {
    "", "changeme", "change-me", "your-key-here", "replace-me",
    "xxx", "todo", "example", "placeholder", "none", "null",
}

#: Names that mean "this file is a key" even when the CONTENT scan cannot read
#: it (a DER/PKCS#12 blob is binary, so sniffing alone would skip it). Kept
#: specific on purpose: `...password...` was tried and matched
#: `backend/tests/test_password_leakage.py`, and a guard that cries wolf gets
#: switched off.
KEY_ISH_BASENAMES = re.compile(
    r"(^|/)(id_rsa|id_dsa|id_ecdsa|id_ed25519|deploy[_-]?key|private[_-]?key"
    r"|secret[_-]?key|signing[_-]?key|service[_-]?account)(\.[a-z0-9]+)?$",
    re.IGNORECASE,
)

#: A file is TEXT if it decodes as UTF-8 and contains no NUL byte. This replaced
#: a suffix allow-list.
#:
#: WHY THE ALLOW-LIST WAS THE BUG. The content scan ran only for files whose
#: suffix was in `TEXT_SUFFIXES`, so the gate skipped exactly the inputs it exists
#: to catch: a tracked file named `deploy_key` holding a PEM private key was NOT
#: scanned (MEASURED: exit 0), while the same bytes in `notes.txt` were
#: (MEASURED: exit 1). An allow-list fails OPEN on every name nobody thought of,
#: and "private key block" is in this tool's own docstring.
_NUL_BYTES = b"\x00"
SNIFF_BYTES = 8192


def looks_like_text(data: bytes) -> bool:
    """Sniff the CONTENT — the file's name is not evidence about its bytes."""
    if _NUL_BYTES in data[:SNIFF_BYTES]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def is_placeholder(match: str) -> bool:
    return match.strip().strip("\"'").lower() in PLACEHOLDER_VALUES


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    files = tracked_files()
    if not files:
        print("FAIL: git ls-files returned nothing — not a git checkout?")
        return 1

    print(f"repo-hygiene: inspecting {len(files)} tracked file(s)")

    scanned = 0
    skipped_binary = 0

    for rel in files:
        path = ROOT / rel

        # 1. forbidden paths
        for pat, why in FORBIDDEN_PATH_PATTERNS:
            if re.search(pat, rel):
                errors.append(f"{rel}: forbidden ({why})")
                break
        # 1b. key-ish names, whatever the content turns out to be
        if KEY_ISH_BASENAMES.search(rel):
            errors.append(f"{rel}: forbidden (name says key material)")

        if not path.is_file():
            continue

        # 2. size
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            errors.append(f"{rel}: {size / 1024 / 1024:.2f} MiB exceeds "
                          f"{MAX_FILE_BYTES / 1024 / 1024:.0f} MiB limit")
            continue  # already a finding; do not read a huge file into memory

        # 3. content scan — SNIFFED, not gated by the file name
        try:
            data = path.read_bytes()
        except OSError as exc:
            warnings.append(f"{rel}: unreadable ({exc}), skipped content scan")
            continue
        if not looks_like_text(data):
            skipped_binary += 1
            continue
        scanned += 1
        text = data.decode("utf-8")

        for pat, why in SECRET_PATTERNS:
            for m in re.finditer(pat, text):
                if is_placeholder(m.group(0)):
                    continue
                line = text[: m.start()].count("\n") + 1
                errors.append(f"{rel}:{line}: possible {why}")

    print(f"  content-scanned {scanned} file(s); "
          f"skipped {skipped_binary} binary file(s)")
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    print()
    print(f"repo-hygiene: {len(files)} file(s) checked, "
          f"{len(errors)} error(s), {len(warnings)} warning(s)")
    print("scope: tracked-path patterns, key-ish basenames, file size > 1 MiB, and "
          "secret-shaped strings in every tracked file whose CONTENT sniffs as text "
          "(UTF-8, no NUL byte) — binary content is NOT scanned.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
