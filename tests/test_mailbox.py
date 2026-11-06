"""Behavioral contracts for intra-job file inboxes (GH #467)."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from marianne.core import mailbox
from marianne.core.config.workspace import mailbox_root


def _write_many(root: Path, sender: int) -> None:
    for index in range(15):
        mailbox.send_message(root, sender, 3, f"{sender}:{index}:" + "λ" * 5000)


def test_round_trip_cursor_and_empty_inbox(tmp_path: Path) -> None:
    root = mailbox_root(tmp_path)
    assert root == tmp_path / ".marianne-scratch" / "mailbox"
    assert mailbox.read_messages(root, 2) == []
    first = mailbox.send_message(root, 1, 2, "hello\nsibling λ")
    second = mailbox.send_message(root, 3, 2, "next")
    assert first.id < second.id
    assert mailbox.read_messages(root, 2) == [first, second]
    assert (first.sender, first.recipient, first.body) == (1, 2, "hello\nsibling λ")
    assert first.timestamp.endswith("+00:00")
    assert mailbox.read_messages(root, 2, since=first.id) == [second]
    assert mailbox.read_messages(root, 2, since=second.id) == []
    assert mailbox.read_messages(root, 1) == []


def test_concurrent_writers_preserve_large_records_and_cursor_order(tmp_path: Path) -> None:
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_write_many, tmp_path, sender) for sender in (1, 2)]
        for future in futures:
            future.result(timeout=10)
    messages = mailbox.read_messages(tmp_path, 3)
    assert len(messages) == 30
    assert len({m.id for m in messages}) == 30
    assert [m.id for m in messages] == sorted(m.id for m in messages)
    for sender in (1, 2):
        assert [m.body for m in messages if m.sender == sender] == [
            f"{sender}:{index}:" + "λ" * 5000 for index in range(15)
        ]
    assert mailbox.read_messages(tmp_path, 3, since=messages[9].id) == messages[10:]


def test_siblings_ignore_unrelated_files_and_sort_numerically(tmp_path: Path) -> None:
    for name in ("1", "10", "2", "notes", "0", "01"):
        (tmp_path / name).mkdir()
    (tmp_path / "3").write_text("not an inbox")
    assert mailbox.list_sheets(tmp_path) == [1, 2, 10]
    assert mailbox.list_sheets(tmp_path / "missing") == []


def test_traffic_summary_exposes_metadata_without_bodies(tmp_path: Path) -> None:
    assert mailbox.traffic_summary(tmp_path) == {"sends": [], "sheets": {}}
    first = mailbox.send_message(tmp_path, 1, 2, "private body λ")
    second = mailbox.send_message(tmp_path, 2, 1, "reply")
    summary = mailbox.traffic_summary(tmp_path)
    assert "body" not in json.dumps(summary)
    assert "private" not in json.dumps(summary)
    assert summary["sends"] == [
        {"id": second.id, "sender": 2, "recipient": 1,
         "bytes": 5, "timestamp": second.timestamp},
        {"id": first.id, "sender": 1, "recipient": 2,
         "bytes": 15, "timestamp": first.timestamp},
    ]
    assert summary["sheets"]["2"] == {
        "messages": 1, "bytes": 15,
        "first_timestamp": first.timestamp, "last_timestamp": first.timestamp,
    }


@pytest.mark.parametrize("sheet", [0, -1, "../outside", "2/3", "01"])
def test_invalid_sheet_identity_cannot_escape_inbox(tmp_path: Path, sheet: object) -> None:
    with pytest.raises(ValueError, match="positive"):
        mailbox.send_message(tmp_path, 1, sheet, "no")
    with pytest.raises(ValueError, match="positive"):
        mailbox.read_messages(tmp_path, sheet)
    assert list(tmp_path.iterdir()) == []


def test_partial_record_is_not_exposed_to_pollers(tmp_path: Path) -> None:
    message = mailbox.send_message(tmp_path, 1, 2, "complete")
    inbox = tmp_path / "2" / "inbox.jsonl"
    with inbox.open("ab") as stream:
        stream.write(b'{"body":"unfinished')
    assert mailbox.read_messages(tmp_path, 2) == [message]
    with pytest.raises(ValueError, match="incomplete"):
        mailbox.send_message(tmp_path, 1, 2, "cannot hide the broken record")


def test_missing_context_refuses_to_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("MARIANNE_MAILBOX", "MARIANNE_SCRATCH", "MARIANNE_SHEET"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="job context"):
        mailbox.context_from_env()
    monkeypatch.setenv("MARIANNE_SCRATCH", "/job/scratch")
    with pytest.raises(ValueError, match="MARIANNE_SHEET"):
        mailbox.context_from_env()
    monkeypatch.setenv("MARIANNE_SHEET", "2")
    assert mailbox.context_from_env() == (Path("/job/scratch/mailbox"), 2)
    monkeypatch.setenv("MARIANNE_MAILBOX", "/explicit/mailbox")
    assert mailbox.context_from_env() == (Path("/explicit/mailbox"), 2)


def test_invalid_complete_record_reports_location_without_body(tmp_path: Path) -> None:
    mailbox.send_message(tmp_path, 1, 2, "first")
    with (tmp_path / "2" / "inbox.jsonl").open("ab") as stream:
        stream.write(b'{"private body": invalid}\n')
    with pytest.raises(ValueError, match="Invalid mailbox record at line 2") as error:
        mailbox.read_messages(tmp_path, 2)
    assert "private body" not in str(error.value)


def test_negative_cursor_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        mailbox.read_messages(tmp_path, 1, since=-1)


def test_merge_traffic_deduplicates_kept_inbox_and_preserves_resumed_sends(tmp_path):
    import shutil

    from marianne.core.mailbox import merge_traffic, send_message, traffic_summary

    send_message(tmp_path, 1, 2, "original body")
    first = traffic_summary(tmp_path)
    assert merge_traffic(first, first)["sheets"]["2"]["messages"] == 1
    shutil.rmtree(tmp_path / "2")
    send_message(tmp_path, 2, 1, "reply after resume")
    merged = merge_traffic(first, traffic_summary(tmp_path))
    assert len(merged["sends"]) == 2
    assert set(merged["sheets"]) == {"1", "2"}
    assert "original body" not in str(merged)
    assert "reply after resume" not in str(merged)
