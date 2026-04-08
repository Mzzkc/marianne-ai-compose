"""Prompt templating for Marianne jobs.

Handles Jinja2 templates, completion prompt generation, and dynamic preambles.
"""

from marianne.prompts.preamble import build_preamble
from marianne.prompts.templating import (
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
