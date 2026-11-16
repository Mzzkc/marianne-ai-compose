"""PromptRenderer records a hash-and-size receipt of every source (GH #546)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from marianne.core.config.job import InjectionCategory, InjectionItem, PromptConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.prompt import PromptRenderer
from marianne.daemon.baton.state import AttemptContext, AttemptMode


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _item(kind: str, value: str, **kw) -> InjectionItem:
    return InjectionItem(**{kind: value}, **{"as": InjectionCategory.CONTEXT}, **kw)


def _render(tmp_path: Path, *, prelude=(), cadenza=(), template="Do the work.", **kw):
    sheet = Sheet(
        num=1, movement=1, voice=1, voice_count=1, workspace=tmp_path,
        instrument_name="claude-code", prompt_template=template,
        prelude=list(prelude), cadenza=list(cadenza), timeout_seconds=60,
    )
    renderer = PromptRenderer(
        prompt_config=PromptConfig(), total_sheets=1, total_stages=1,
        parallel_enabled=False,
    )
    return renderer.render(
        sheet, AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL), **kw
    )


def test_receipt_covers_prelude_directory_capped_and_template(tmp_path: Path) -> None:
    identity = tmp_path / "identity.md"
    identity.write_text("I am Canyon.\n")
    cadenza = tmp_path / "active"
    cadenza.mkdir()
    (cadenza / "a.md").write_text("alpha ledger\n")
    (cadenza / "b.md").write_text("beta ledger\n")
    big = tmp_path / "big"
    big.mkdir()
    (big / "huge.md").write_text("x" * 5000)

    result = _render(
        tmp_path,
        prelude=[_item("file", str(identity))],
        cadenza=[
            _item("directory", str(cadenza)),
            _item("directory", str(big), max_bytes=100),
            _item("file", str(tmp_path / "gone.md")),
        ],
    )
    receipt = result.prompt_receipt
    assert receipt is not None
    by_path = {(s.kind, s.resolved_path): s for s in receipt.sources}

    ident = by_path[("prelude", str(identity))]
    assert ident.path == str(identity)
    assert ident.sha256 == _sha(b"I am Canyon.\n")
    assert ident.bytes == len(b"I am Canyon.\n")
    assert ident.dropped_reason is None and ident.truncated is False

    # a directory cadenza is one source per file, header bytes included
    a = by_path[("cadenza", str((cadenza / "a.md").resolve()))]
    assert a.path == str(cadenza)
    assert a.bytes == len(b"--- Input: a.md ---\n\nalpha ledger\n")
    assert a.sha256 == _sha(b"--- Input: a.md ---\n\nalpha ledger\n")

    # the capped optional item is dropped, with its measured size, and costs 0 bytes
    capped = [s for s in receipt.sources if s.path == str(big)]
    assert len(capped) == 1
    assert capped[0].bytes == 0 and capped[0].sha256 is None
    assert capped[0].dropped_reason == "over_max_bytes: measured 5000 > max_bytes 100"
    assert "xxxxx" not in result.prompt

    gone = [s for s in receipt.sources if s.path == str(tmp_path / "gone.md")]
    assert gone[0].dropped_reason == "not_found"

    template = receipt.sources[-1]
    assert template.kind == "template"
    assert template.path == "<inline prompt.template>"
    assert template.sha256 == _sha(b"Do the work.")

    prompt_bytes = result.prompt.encode()
    assert receipt.total_bytes == len(prompt_bytes)
    assert receipt.prompt_sha256 == _sha(prompt_bytes)
    delivered = sum(s.bytes for s in receipt.sources if s.kind != "template")
    assert receipt.template_bytes == len(prompt_bytes) - delivered
    assert receipt.estimated_tokens >= len(prompt_bytes) // 4
    # the receipt names bytes and hashes; the prompt text is not in it
    dumped = receipt.model_dump_json()
    for text in ("I am Canyon", "alpha ledger", "Do the work"):
        assert text not in dumped


def test_declared_path_keeps_the_tilde_form(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "id.md").write_text("hello")
    receipt = _render(tmp_path, prelude=[_item("file", "~/id.md")]).prompt_receipt
    src = receipt.sources[0]
    assert src.path == "~/id.md"
    assert src.resolved_path == str((tmp_path / "id.md").resolve())


def test_technique_and_memory_blocks_are_sources(tmp_path: Path) -> None:
    receipt = _render(
        tmp_path,
        technique_manifest="TECH-MANIFEST",
        technique_skill_docs={"canyon-specialist": "SKILL BODY"},
        memory_manifest="MEMORY BLOCK",
    ).prompt_receipt
    kinds = {s.path: (s.kind, s.bytes, s.sha256) for s in receipt.sources}
    assert kinds["technique:canyon-specialist"] == ("technique", 10, _sha(b"SKILL BODY"))
    assert kinds["technique-manifest"][0] == "technique"
    assert kinds["memory:manifest"] == ("memory", 12, _sha(b"MEMORY BLOCK"))


def test_template_file_is_a_template_source(tmp_path: Path) -> None:
    tpl = tmp_path / "sheet.j2"
    tpl.write_text("From file {{ sheet_num }}")
    sheet = Sheet(
        num=1, movement=1, voice=1, voice_count=1, workspace=tmp_path,
        instrument_name="claude-code", template_file=tpl, timeout_seconds=60,
    )
    renderer = PromptRenderer(
        prompt_config=PromptConfig(), total_sheets=1, total_stages=1,
        parallel_enabled=False,
    )
    rendered = renderer.render(sheet, AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL))
    template = rendered.prompt_receipt.sources[-1]
    assert template.kind == "template"
    assert template.path == str(tpl)
    assert template.sha256 == _sha(tpl.read_bytes())


def test_raw_prompt_marks_resolved_attachments_as_not_delivered(tmp_path: Path) -> None:
    (tmp_path / "id.md").write_text("identity")
    result = _render(tmp_path, prelude=[_item("file", str(tmp_path / "id.md"))],
                     raw_prompt=True)
    receipt = result.prompt_receipt
    ident = receipt.sources[0]
    assert ident.dropped_reason == "raw_prompt" and ident.bytes == 0
    assert receipt.total_bytes == len(result.prompt.encode())
    assert "identity" not in result.prompt
