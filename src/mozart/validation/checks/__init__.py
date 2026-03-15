"""Validation check implementations.

This module contains all the built-in validation checks organized by category:
- jinja: Template syntax and undefined variable checks
- paths: File and directory existence checks
- config: Configuration structure and value checks
"""

from mozart.validation.checks.best_practices import (
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
from mozart.validation.checks.config import (
    EmptyPatternCheck,
    RegexPatternCheck,
    TimeoutRangeCheck,
    ValidationTypeCheck,
    VersionReferenceCheck,
)
from mozart.validation.checks.jinja import (
    JinjaSyntaxCheck,
    JinjaUndefinedVariableCheck,
)
from mozart.validation.checks.paths import (
    PreludeCadenzaFileCheck,
    SkillFilesExistCheck,
    SystemPromptFileCheck,
    TemplateFileExistsCheck,
    WorkingDirectoryCheck,
    WorkspaceParentExistsCheck,
)

__all__ = [
    # Jinja checks
    "JinjaSyntaxCheck",
    "JinjaUndefinedVariableCheck",
    # Path checks
    "WorkspaceParentExistsCheck",
    "TemplateFileExistsCheck",
    "SystemPromptFileCheck",
    "WorkingDirectoryCheck",
    "PreludeCadenzaFileCheck",
    "SkillFilesExistCheck",
    # Config checks
    "RegexPatternCheck",
    "ValidationTypeCheck",
    "TimeoutRangeCheck",
    "EmptyPatternCheck",
    "VersionReferenceCheck",
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
]
