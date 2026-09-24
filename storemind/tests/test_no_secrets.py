"""Secrets never go in git (CLAUDE.md, CLAUDE_CODE_PROMPT_V2 section 0).

Fails if a secrets file is tracked or staged, or if a tracked/staged text file
contains something that looks like a real token.  Runs with the normal test
suite, so it is checked before every commit.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_NAMES = re.compile(r"(^|/)(secrets\.ya?ml|\.env(\..+)?)$")
ALLOWED_NAMES = {".env.example"}

TOKEN_PATTERNS = [
    re.compile(r"(?i)(api[_-]?token|QAI_HUB_API_TOKEN)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{24,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".toml", ".cfg", ".ini",
                 ".ps1", ".sh", ".c", ".h", ".html", ".js", ".css", ".csv"}


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


pytestmark = pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                                reason="not a git checkout")


def _candidates() -> set[str]:
    return set(_git("ls-files")) | set(_git("diff", "--cached", "--name-only"))


def test_no_secrets_file_is_tracked_or_staged():
    bad = [f for f in _candidates()
           if FORBIDDEN_NAMES.search(f) and Path(f).name not in ALLOWED_NAMES]
    assert not bad, f"secrets file in git: {bad}"


def test_no_token_like_string_in_tracked_text():
    me = Path(__file__).resolve()
    hits = []
    for name in _candidates():
        path = ROOT / name
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file() or path.resolve() == me:
            continue
        if path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{name}: {p.pattern[:30]}" for p in TOKEN_PATTERNS if p.search(text)]
    assert not hits, f"token-like strings found: {hits}"


def test_gitignore_covers_secrets():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in ("configs/secrets.yaml", "storemind/configs/secrets.yaml", ".env"):
        assert entry in ignore
