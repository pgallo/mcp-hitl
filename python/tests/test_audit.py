from __future__ import annotations

import json
from pathlib import Path

import pytest

from hitl_server.audit import GENESIS_HASH, AuditLog, entry_hash, main, verify_entries
from hitl_server.spec import spec_dir


def _log(n: int = 4, path: Path | None = None) -> AuditLog:
    log = AuditLog(path)
    for i in range(n):
        log.append(at=f"2026-03-02T09:00:0{i}Z", event="test.event", actor="tester", subject=f"S-{i}", data={"i": i})
    return log


def test_chain_links_and_verifies() -> None:
    log = _log()
    entries = log.entries()
    assert entries[0]["prev_hash"] == GENESIS_HASH
    for prev, cur in zip(entries, entries[1:], strict=False):
        assert cur["prev_hash"] == prev["hash"]
    assert log.verify().valid
    assert log.verify().head_hash == log.head_hash


def test_entries_are_copies() -> None:
    log = _log()
    log.entries()[0]["actor"] = "mallory"
    assert log.verify().valid


@pytest.mark.parametrize(
    ("mutate", "bad_seq", "problem"),
    [
        (lambda es: es[1]["data"].update(i=99), 2, "content"),
        (lambda es: es.pop(1), 2, "seq"),
        (lambda es: es.insert(0, es.pop(2)), 1, "seq"),
        (lambda es: es[2].update(prev_hash=GENESIS_HASH), 3, "prev_hash"),
    ],
    ids=["edit", "delete", "reorder", "relink"],
)
def test_tampering_is_located(mutate, bad_seq: int, problem: str) -> None:  # type: ignore[no-untyped-def]
    entries = _log().entries()
    mutate(entries)
    result = verify_entries(entries)
    assert not result.valid
    assert result.first_bad_seq == bad_seq
    assert problem in (result.problem or "")


def test_recomputing_one_hash_still_breaks_the_next_link() -> None:
    entries = _log().entries()
    entries[1]["data"] = {"i": 99}
    entries[1]["hash"] = entry_hash(entries[1])
    result = verify_entries(entries)
    assert result.first_bad_seq == 3
    assert result.problem == "prev_hash does not match previous entry"


def test_persistence_round_trip_and_refusal_to_extend_broken_chain(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(3, path)
    reopened = AuditLog(path)
    assert len(reopened) == 3 and reopened.head_hash == log.head_hash
    reopened.append(at="2026-03-02T10:00:00Z", event="test.event", actor="tester", subject="S-3", data={})
    assert main([str(path)]) == 0

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["actor"] = "mallory"
    lines[0] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="broken audit chain at seq 1"):
        AuditLog(path)
    assert main([str(path)]) == 1


def test_reference_audit_fixture_verifies() -> None:
    lines = (spec_dir() / "fixtures" / "reference_audit.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    scenario = json.loads((spec_dir() / "fixtures" / "reference_scenario.json").read_text(encoding="utf-8"))
    result = verify_entries(entries)
    assert result.valid
    assert result.head_hash == scenario["head_hash"]
