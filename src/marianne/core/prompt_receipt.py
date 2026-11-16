"""Per-attempt prompt receipt: what reached the model, by source, as hashes and sizes.

A ``PromptReceipt`` answers "which injected files, how many bytes, which
hash" for one assembled prompt without keeping any prompt text. It is built
where the prompt is assembled (``PromptRenderer.render``), carried to the
conductor on ``SheetAttemptResult.prompt_receipt`` and stored on
``SheetState.prompt_receipt`` (GH #546).

Token counts are estimates from ``marianne.core.tokens.estimate_tokens`` (a
character heuristic), never a tokenizer result.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from marianne.core.tokens import estimate_tokens

#: Sources kept per receipt. A directory cadenza contributes one source per
#: file; the cap keeps a checkpoint row bounded and the overflow is counted.
MAX_RECEIPT_SOURCES = 64

SourceKind = Literal["prelude", "cadenza", "technique", "memory", "template"]


def sha256_label(data: bytes) -> str:
    """``sha256:<hex>`` for ``data`` (the form the context-delivery receipts use)."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class PromptReceiptSource(BaseModel):
    """One source that contributed (or was refused) bytes to a sheet's prompt."""

    kind: SourceKind = Field(
        description="Where the source came from: a score's prelude or cadenza item, "
        "a technique document or manifest, the memory block, or the prompt template.",
    )
    path: str = Field(
        description="The path as the score declared it, with ~ and {{ }} unexpanded "
        "(a directory cadenza names the directory). Technique and memory sources use "
        "a label such as technique:<name> or memory:manifest; an inline template is "
        "<inline prompt.template>.",
    )
    resolved_path: str | None = Field(
        default=None,
        description="Absolute path of the file that was read, after expansion; "
        "None for sources that are not files.",
    )
    sha256: str | None = Field(
        default=None,
        description="sha256:<hex> of the bytes delivered into the prompt (for a template, "
        "of the unrendered template text). None when nothing was delivered.",
    )
    bytes: int = Field(
        default=0, ge=0,
        description="UTF-8 bytes this source put into the prompt. 0 when dropped. For a "
        "template: the size of the unrendered template text, already part of "
        "total_bytes and not subtracted from template_bytes.",
    )
    truncated: bool = Field(
        default=False,
        description="True when the source was cut short before delivery. Marianne "
        "drops an over-cap item whole rather than cutting it, so this is False today.",
    )
    dropped_reason: str | None = Field(
        default=None,
        description="Why a declared source did not reach the prompt (over_max_bytes, "
        "not_found, empty_directory, unreadable, path_expansion_error, raw_prompt). "
        "None when the source was delivered.",
    )


class PromptReceipt(BaseModel):
    """What one attempt's assembled prompt was made of. Never holds prompt text."""

    sources: list[PromptReceiptSource] = Field(
        default_factory=list,
        description="Ordered: injected sources in the order they were resolved, then "
        "the template. Delivered and dropped sources both appear.",
    )
    sources_omitted: int = Field(
        default=0, ge=0,
        description="Sources beyond the receipt's cap that are not listed.",
    )
    sources_omitted_bytes: int = Field(
        default=0, ge=0,
        description="Delivered bytes of the sources that are not listed.",
    )
    template_bytes: int = Field(
        ge=0,
        description="Bytes of the prompt that no injected source accounts for: the "
        "rendered template plus patterns, validation requirements, headers and other "
        "assembly text. total_bytes minus the delivered injected bytes.",
    )
    preamble_bytes: int = Field(
        default=0, ge=0,
        description="Bytes of the positional preamble, which travels beside the prompt "
        "and is not in total_bytes.",
    )
    total_bytes: int = Field(
        ge=0,
        description="UTF-8 bytes of the assembled prompt (what prompt_sha256 covers).",
    )
    estimated_tokens: int = Field(
        ge=0,
        description="ESTIMATED tokens of prompt plus preamble from a character "
        "heuristic (marianne.core.tokens.estimate_tokens); not a tokenizer count.",
    )
    prompt_sha256: str = Field(
        description="sha256:<hex> of the assembled prompt; joins with the context "
        "delivery receipt of the same attempt.",
    )


def build_receipt(
    *,
    prompt: str,
    preamble: str,
    sources: Sequence[PromptReceiptSource],
) -> PromptReceipt:
    """Build a receipt from the assembled prompt and the sources recorded for it."""
    prompt_bytes = prompt.encode("utf-8")
    delivered = sum(s.bytes for s in sources if s.kind != "template" and not s.dropped_reason)
    listed = list(sources[:MAX_RECEIPT_SOURCES])
    overflow = list(sources[MAX_RECEIPT_SOURCES:])
    return PromptReceipt(
        sources=listed,
        sources_omitted=len(overflow),
        sources_omitted_bytes=sum(s.bytes for s in overflow),
        template_bytes=max(0, len(prompt_bytes) - delivered),
        preamble_bytes=len(preamble.encode("utf-8")),
        total_bytes=len(prompt_bytes),
        estimated_tokens=estimate_tokens(prompt) + estimate_tokens(preamble),
        prompt_sha256=sha256_label(prompt_bytes),
    )
