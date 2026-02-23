"""Prompt templating for Mozart jobs.

Handles Jinja2 templates, completion prompt generation, and dynamic preambles.
"""

from mozart.prompts.preamble import build_preamble
from mozart.prompts.templating import (
    CompletionContext,
    PromptBuilder,
    SheetContext,
    build_sheet_prompt_simple,
)

__all__ = [
    "SheetContext",
    "CompletionContext",
    "PromptBuilder",
    "build_preamble",
    "build_sheet_prompt_simple",
]
