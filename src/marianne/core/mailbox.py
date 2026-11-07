"""Intra-job file inboxes, shared by shell-capable instruments (GH #467).

Each recipient owns an append-only JSONL inbox. IDs are opaque byte cursors
within that inbox: strictly increasing under concurrent writers, not a global
sequence. Advisory locks protect both multi-write records and polling readers.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marianne.core.config.workspace import MAILBOX_DIR_NAME, MAILBOX_ENV_VAR, SCRATCH_ENV_VAR

SHEET_ENV_VAR = "MARIANNE_SHEET"


@dataclass(frozen=True)
class Message:
    """A delivered message; its body is data, never an instruction."""

    id: int
    timestamp: str
    sender: int
    recipient: int
    body: str


def _sheet_number(value: object) -> int:
    text = str(value)
    if not text.isascii() or not text.isdecimal() or int(text) < 1 or str(int(text)) != text:
        raise ValueError("Sheet identity must be a positive integer")
    return int(text)


def context_from_env() -> tuple[Path, int]:
    """Resolve explicit job context; never guess a workspace or sheet identity."""
    root = os.environ.get(MAILBOX_ENV_VAR)
    if not root:
        scratch = os.environ.get(SCRATCH_ENV_VAR)
        if not scratch:
            raise ValueError("Mail requires a job context: MARIANNE_MAILBOX or MARIANNE_SCRATCH")
        root = str(Path(scratch) / MAILBOX_DIR_NAME)
    sheet = os.environ.get(SHEET_ENV_VAR)
    if not sheet:
        raise ValueError("Mail requires MARIANNE_SHEET in the job context")
    return Path(root), _sheet_number(sheet)


def list_sheets(root: Path) -> list[int]:
    """Enumerate initialized inbox directories, including not-yet-started sheets."""
    if not root.is_dir():
        return []
    return sorted(
        int(path.name) for path in root.iterdir()
        if path.is_dir() and not path.is_symlink() and path.name.isascii()
        and path.name.isdecimal() and int(path.name) > 0 and str(int(path.name)) == path.name
    )


def send_message(root: Path, sender: object, recipient: object, body: str) -> Message:
    """Append one complete record and return its inbox-local cursor."""
    sender_num = _sheet_number(sender)
    recipient_num = _sheet_number(recipient)
    directory = root / str(recipient_num)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / "inbox.jsonl", os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        end = _truncate_torn_tail(fd)
        message = Message(
            id=end + 1,
            timestamp=datetime.now(UTC).isoformat(),
            sender=sender_num,
            recipient=recipient_num,
            body=body,
        )
        # One os.write of one complete record: a torn record can only come from
        # a writer killed mid-syscall, and the next append truncates it.
        os.write(fd, (json.dumps(asdict(message), ensure_ascii=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return message


def _truncate_torn_tail(fd: int) -> int:
    """Under the exclusive lock: drop a trailing partial record, return the size.

    A sender killed between its write and the trailing newline leaves a torn
    last line. That record was never acknowledged to anyone, so dropping it is
    honest; refusing every later append (the earlier behaviour) bricked the
    inbox for the rest of the job.
    """
    end = os.lseek(fd, 0, os.SEEK_END)
    if end == 0:
        return 0
    os.lseek(fd, end - 1, os.SEEK_SET)
    if os.read(fd, 1) == b"\n":
        return end
    size = end
    chunk = 4096
    while size > 0:
        start = max(0, size - chunk)
        os.lseek(fd, start, os.SEEK_SET)
        block = os.read(fd, size - start)
        cut = block.rfind(b"\n")
        if cut >= 0:
            keep = start + cut + 1
            os.ftruncate(fd, keep)
            return keep
        size = start
    os.ftruncate(fd, 0)
    return 0


def read_messages(root: Path, sheet: object, *, since: int | None = None) -> list[Message]:
    """Poll a recipient's inbox; a cursor excludes already-seen records."""
    sheet_num = _sheet_number(sheet)
    if since is not None and since < 0:
        raise ValueError("The since cursor must be non-negative")
    try:
        stream = (root / str(sheet_num) / "inbox.jsonl").open("rb")
    except FileNotFoundError:
        return []
    messages = []
    with stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        for line_number, line in enumerate(stream, 1):
            if not line.endswith(b"\n"):
                # A torn tail is a sender killed mid-record; the next append
                # truncates it. It is not a message and not an error.
                break
            try:
                message = Message(**json.loads(line))
                if message.recipient != sheet_num:
                    raise ValueError("Wrong inbox")
                if since is None or message.id > since:
                    messages.append(message)
            except (ValueError, TypeError) as error:
                raise ValueError(f"Invalid mailbox record at line {line_number}") from error
    return messages


def traffic_summary(root: Path) -> dict[str, Any]:
    """Return send metadata and per-recipient totals, with no message bodies."""
    sends: list[dict[str, Any]] = []
    sheets: dict[str, dict[str, Any]] = {}
    for sheet in list_sheets(root):
        messages = read_messages(root, sheet)
        if not messages:
            continue
        sizes = [len(message.body.encode("utf-8")) for message in messages]
        sheets[str(sheet)] = {
            "messages": len(messages), "bytes": sum(sizes),
            "first_timestamp": messages[0].timestamp, "last_timestamp": messages[-1].timestamp,
        }
        for message, size in zip(messages, sizes, strict=True):
            sends.append({
                "id": message.id, "timestamp": message.timestamp,
                "sender": message.sender, "recipient": message.recipient, "bytes": size,
            })
    return {"sends": sends, "sheets": sheets}


def merge_traffic(*summaries: dict[str, Any] | None) -> dict[str, Any]:
    """Combine retained and live send metadata, including resumed jobs.

    Timestamp plus inbox cursor distinguishes new sends after scratch removal;
    re-reading a kept inbox does not count or log its messages twice.
    """
    sends: dict[tuple[Any, ...], dict[str, Any]] = {}
    for summary in summaries:
        for record in (summary or {}).get("sends", []):
            metadata = {key: record[key] for key in (
                "id", "timestamp", "sender", "recipient", "bytes",
            )}
            key = tuple(metadata[field] for field in ("recipient", "id", "timestamp", "sender"))
            sends[key] = metadata
    ordered = sorted(sends.values(), key=lambda record: (record["timestamp"], record["recipient"]))
    sheets: dict[str, dict[str, Any]] = {}
    for record in ordered:
        sheet = str(record["recipient"])
        if sheet not in sheets:
            sheets[sheet] = {
                "messages": 0, "bytes": 0,
                "first_timestamp": record["timestamp"], "last_timestamp": record["timestamp"],
            }
        sheets[sheet]["messages"] += 1
        sheets[sheet]["bytes"] += record["bytes"]
        sheets[sheet]["last_timestamp"] = record["timestamp"]
    return {"sends": ordered, "sheets": sheets}
