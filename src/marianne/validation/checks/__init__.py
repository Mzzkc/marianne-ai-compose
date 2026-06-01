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
    MissingDisableMcpCheck,
    MissingSkipPermissionsCheck,
    NoValidationsCheck,
    SkipWhenSheetRangeCheck,
    VariableShadowingCheck,
)
from marianne.validation.checks.config import (
    EmptyPatternCheck,
    FoldedCommandScalarCheck,
    InstrumentFallbackCheck,
    InstrumentNameCheck,
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
    PreludeCadenzaFileCheck,
    SkillFilesExistCheck,
    SystemPromptFileCheck,
    TemplateFileExistsCheck,
    WorkingDirectoryCheck,
    WorkspaceParentExistsCheck,
)
from marianne.validation.checks.techniques import (
    TechniqueMcpInstrumentCheck,
    TechniqueSkillPathCheck,
)

__all__ = [
    # Jinja checks
    "JinjaSyntaxCheck",
    "JinjaUndefinedVariableCheck",
    "FanOutStringFilterCheck",
    "BashArrayLengthCheck",
    # Path checks
    "WorkspaceParentExistsCheck",
    "TemplateFileExistsCheck",
    "SystemPromptFileCheck",
    "WorkingDirectoryCheck",
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
    "InstrumentNameCheck",
    # Best-practice checks
    "JinjaInValidationPathCheck",
    "FormatSyntaxInTemplateCheck",
    "NoValidationsCheck",
    "MissingSkipPermissionsCheck",
    "FileExistsOnlyCheck",
    "FanOutWithoutDependenciesCheck",
    "FanOutWithoutParallelCheck",
    "VariableShadowingCheck",
    "MissingDisableMcpCheck",
    "SkipWhenSheetRangeCheck",
    # Technique checks
    "TechniqueSkillPathCheck",
    "TechniqueMcpInstrumentCheck",
]
