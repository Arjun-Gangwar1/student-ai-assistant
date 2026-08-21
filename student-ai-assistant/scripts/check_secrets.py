#!/usr/bin/env python3
"""
Pre-push secret scanner.

Fails if anything that looks like a live credential appears in a tracked file.
Run manually, or wire it up as a pre-commit hook:

    ln -s ../../scripts/check_secrets.py .git/hooks/pre-commit

Exit codes:  0 = clean,  1 = secrets found,  2 = could not run
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (label, compiled pattern) — each matches a credential *shape*, not a known value,
# so this keeps working after rotation.
PATTERNS = [
    ("Groq API key",            re.compile(r"\bgsk_[A-Za-z0-9]{40,}")),
    ("Google OAuth secret",     re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{20,}")),
    ("Telegram bot token",      re.compile(r"\b\d{8,12}:AA[A-Za-z0-9_\-]{30,}")),
    ("JWT / Supabase key",      re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key",          re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("AWS access key id",       re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Postgres URL w/ password", re.compile(r"postgres(?:ql)?://[^:\s/]+:[^@\s]{6,}@(?!localhost|127\.0\.0\.1|db[:/])")),
    ("Private key block",       re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

# Files that legitimately contain credential-shaped example text.
ALLOWLIST_SUFFIXES = (".md", ".txt", ".pdf", ".docx")
ALLOWLIST_NAMES = {".env.example", "check_secrets.py"}

# Placeholder values that are safe by construction.
PLACEHOLDER = re.compile(r"replace-me|your[-_]|example|placeholder|xxx+|\.\.\.$", re.I)


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("check_secrets: not a git repo (or git missing) — scanning nothing", file=sys.stderr)
        sys.exit(2)
    return [ROOT / p for p in out.decode().split("\0") if p]


def main() -> int:
    findings: list[str] = []

    for path in tracked_files():
        if path.name in ALLOWLIST_NAMES:
            continue
        # Research/planning docs quote truncated tokens as illustrations.
        if path.suffix.lower() in ALLOWLIST_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue

        for label, pattern in PATTERNS:
            for m in pattern.finditer(text):
                snippet = m.group(0)
                if PLACEHOLDER.search(snippet):
                    continue
                line_no = text[: m.start()].count("\n") + 1
                rel = path.relative_to(ROOT)
                findings.append(f"  {rel}:{line_no}  {label}: {snippet[:24]}…")

    if findings:
        print("\n\033[31m✗ Possible live credentials in tracked files:\033[0m\n")
        print("\n".join(findings))
        print(
            "\nRemove them, then rotate — deleting a secret from a file does not "
            "un-leak it.\nSee docs/SECURITY_ROTATION.md\n"
        )
        return 1

    print("\033[32m✓ check_secrets: no live credentials in tracked files\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
