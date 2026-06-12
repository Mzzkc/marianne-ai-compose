"""Validation check implementations.

This module contains all the built-in validation checks organized by category:
- jinja: Template syntax and undefined variable checks
- paths: File and directory existence checks
- config: Configuration structure and value checks
"""

from marianne.validation.checks.best_practices import (
    FanOutWithoutDependenciesCheck,
    FanOutWithoutParallelCheck,
    FileExistsOnlyCheck,
    FormatSyntaxInTemplateCheck,
    JinjaInValidationPathCheck,
    NoValidationsCheck,
    SkipWhenSheetRangeCheck,
    VariableShadowingCheck,
)
from marianne.validation.checks.config import (
    CodeExecutionSandboxCheck,
    EmptyPatternCheck,
    FoldedCommandScalarCheck,
    InstrumentFallbackCheck,
    InstrumentNameCheck,
    InteractiveSupportCheck,
    NoUsableInstrumentCheck,
    RegexPatternCheck,
    TimeoutRangeCheck,
    ValidationTypeCheck,
    VersionReferenceCheck,
)
from marianne.validation.checks.jinja import (
    BashArrayLengthCheck,
    FanOutStringFilterCheck,
    JinjaSyntaxCheck,
    JinjaUndefinedVariableCheck,
)
from marianne.validation.checks.paths import (
    CadenzaOrderingCheck,
    PreludeCadenzaFileCheck,
    SkillFilesExistCheck,
    TemplateFileExistsCheck,
    WorkspaceParentExistsCheck,
)
from marianne.validation.checks.techniques import (
    TechniqueMcpInstrumentCheck,
    TechniqueSkillPathCheck,
)

__all__ = [
    "CadenzaOrderingCheck",
    "CodeExecutionSandboxCheck",
    # Jinja checks
    "JinjaSyntaxCheck",
    "JinjaUndefinedVariableCheck",
    "FanOutStringFilterCheck",
    "BashArrayLengthCheck",
    # Path checks
    "WorkspaceParentExistsCheck",
    "TemplateFileExistsCheck",
    "PreludeCadenzaFileCheck",
    "SkillFilesExistCheck",
    # Config checks
    "FoldedCommandScalarCheck",
    "RegexPatternCheck",
    "ValidationTypeCheck",
    "TimeoutRangeCheck",
    "EmptyPatternCheck",
    "VersionReferenceCheck",
    "InstrumentFallbackCheck",
    "NoUsableInstrumentCheck",
    "InstrumentNameCheck",
    "InteractiveSupportCheck",
    # Best-practice checks
    "JinjaInValidationPathCheck",
    "FormatSyntaxInTemplateCheck",
    "NoValidationsCheck",
    "FileExistsOnlyCheck",
    "FanOutWithoutDependenciesCheck",
    "FanOutWithoutParallelCheck",
    "VariableShadowingCheck",
    "SkipWhenSheetRangeCheck",
    # Technique checks
    "TechniqueSkillPathCheck",
    "TechniqueMcpInstrumentCheck",
]
