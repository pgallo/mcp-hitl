"""Append-only, hash-chained audit log.

Each entry stores the SHA-256 of the previous entry, and its own hash covers every
field except `hash` itself. Editing, deleting or reordering any entry breaks every
hash after it, so `verify` pinpoints the first bad sequence number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import canonical_json

GENESIS_HASH = "0" * 64


def entry_hash(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    valid: bool
    length: int
    head_hash: str
    first_bad_seq: int | None
    problem: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "length": self.length,
            "head_hash": self.head_hash,
            "first_bad_seq": self.first_bad_seq,
            "problem": self.problem,
        }


def verify_entries(entries: list[dict[str, Any]]) -> VerifyResult:
    prev = GENESIS_HASH
    for index, entry in enumerate(entries, start=1):
        seq = entry.get("seq")
        if seq != index:
            return VerifyResult(False, len(entries), prev, index, f"expected seq {index}, found {seq}")
        if entry.get("prev_hash") != prev:
            return VerifyResult(False, len(entries), prev, index, "prev_hash does not match previous entry")
        if entry_hash(entry) != entry.get("hash"):
            return VerifyResult(False, len(entries), prev, index, "entry content does not match its hash")
        prev = entry["hash"]
    return VerifyResult(True, len(entries), prev, None, None)


class AuditLog:
    """In-memory chain with optional JSON Lines persistence (one entry per line, append mode)."""

    def __init__(self, path: Path | None = None) -> None:
        self._entries: list[dict[str, Any]] = []
        self._path = path
        if path is not None and path.exists():
            loaded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            result = verify_entries(loaded)
            if not result.valid:
                raise ValueError(f"refusing to extend a broken audit chain at seq {result.first_bad_seq}: {result.problem}")
            self._entries = loaded

    @property
    def head_hash(self) -> str:
        return self._entries[-1]["hash"] if self._entries else GENESIS_HASH

    def __len__(self) -> int:
        return len(self._entries)

    def append(self, *, at: str, event: str, actor: str, subject: str, data: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "seq": len(self._entries) + 1,
            "at": at,
            "event": event,
            "actor": actor,
            "subject": subject,
            "data": data,
            "prev_hash": self.head_hash,
        }
        entry["hash"] = entry_hash(entry)
        self._entries.append(entry)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(canonical_json(entry) + "\n")
        return entry

    def entries(self, since_seq: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        selected = [dict(e) for e in self._entries if e["seq"] > since_seq]
        return selected if limit is None else selected[:limit]

    def verify(self) -> VerifyResult:
        return verify_entries(self._entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hitl-audit", description="Verify a JSON Lines audit chain.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    entries = [json.loads(line) for line in args.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = verify_entries(entries)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.valid else 1


if __name__ == "__main__":
    sys.exit(main())
