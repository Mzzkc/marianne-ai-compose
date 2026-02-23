"""Score configuration validation API endpoints."""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError

from mozart.core.config import JobConfig
from mozart.scores.templates import TEMPLATE_FILES, get_template_path, list_templates
from mozart.validation import (
    ValidationRunner,
    create_default_checks,
)

router = APIRouter(prefix="/api/scores", tags=["Score Editor"])


# ============================================================================
# Request/Response Models (Pydantic schemas for API)
# ============================================================================


class ValidateConfigRequest(BaseModel):
    """Request to validate a YAML configuration."""
    content: str = Field(
        ...,
        max_length=1_000_000,
        description="YAML configuration content to validate",
    )
    filename: str = Field("config.yaml", description="Virtual filename for context")
    workspace_path: str | None = Field(
        None, description="Optional workspace path for relative path validation"
    )


class ValidationIssueResponse(BaseModel):
    """Individual validation issue."""
    check_id: str = Field(..., description="Unique check identifier (e.g., V001)")
    severity: str = Field(..., description="ERROR, WARNING, or INFO")
    message: str = Field(..., description="Human-readable issue description")
    line: int | None = Field(None, description="Line number in config file")
    column: int | None = Field(None, description="Column number")
    context: str | None = Field(None, description="Surrounding text context")
    suggestion: str | None = Field(None, description="How to fix the issue")
    auto_fixable: bool = Field(False, description="Can be automatically fixed")


class ValidateConfigResponse(BaseModel):
    """Response from configuration validation."""
    valid: bool = Field(..., description="True if no ERROR-level issues found")
    yaml_syntax_valid: bool = Field(..., description="True if YAML parses correctly")
    schema_valid: bool = Field(..., description="True if Pydantic validation passes")
    issues: list[ValidationIssueResponse] = Field(..., description="All validation issues found")
    counts: dict[str, int] = Field(..., description="Issue counts by severity")
    config_summary: dict[str, Any] | None = Field(None, description="Config summary if valid")
    error_message: str | None = Field(None, description="Fatal error message if parsing failed")


# ============================================================================
# Business Logic Functions
# ============================================================================


def parse_yaml_safely(content: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse YAML content and return data or error message.

    Returns:
        Tuple of (parsed_data, error_message). One will be None.
    """
    try:
        data = yaml.safe_load(content)
        return data, None
    except yaml.YAMLError as e:
        return None, f"YAML syntax error: {e}"


def validate_schema(
    content: str, filename: str = "config.yaml"
) -> tuple[JobConfig | None, str | None]:
    """Validate YAML against JobConfig schema.

    Args:
        content: YAML content string
        filename: Virtual filename for error context

    Returns:
        Tuple of (parsed_config, error_message). One will be None.
    """
    try:
        # Create temporary file for JobConfig.from_yaml() method
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)

        try:
            config = JobConfig.from_yaml(temp_path)
            return config, None
        finally:
            # Clean up temp file
            temp_path.unlink(missing_ok=True)

    except ValidationError as e:
        return None, f"Schema validation failed: {e}"
    except Exception as e:
        return None, f"Configuration error: {e}"


def run_extended_validation(
    config: JobConfig,
    content: str,
    filename: str = "config.yaml",
    workspace_path: str | None = None
) -> list[ValidationIssueResponse]:
    """Run Mozart's extended validation checks.

    Args:
        config: Parsed JobConfig object
        content: Raw YAML content for line number extraction
        filename: Virtual filename
        workspace_path: Optional workspace path for relative path validation.
            Validated with allow-list: must resolve to a path under cwd or
            user home directory. Invalid paths are silently replaced with
            None to fall back to cwd.

    Returns:
        List of validation issues
    """
    # Validate workspace_path to prevent path traversal attacks using allow-list
    if workspace_path is not None:
        ws = Path(workspace_path).resolve()
        cwd = Path.cwd().resolve()
        home = Path.home().resolve()
        # Allow only paths under cwd or user home directory
        if not (ws.is_relative_to(cwd) or ws.is_relative_to(home)):
            _logger.warning("Rejected workspace_path outside allowed roots: %s", workspace_path)
            workspace_path = None

    # Create a virtual config path for the validator
    # Use workspace_path as base if provided, otherwise use current directory
    config_path = (
        Path(workspace_path) / filename if workspace_path else Path.cwd() / filename
    )

    # Run validation using Mozart's built-in system
    runner = ValidationRunner(create_default_checks())
    issues = runner.validate(config, config_path, content)

    # Convert to response objects
    return [
        ValidationIssueResponse(
            check_id=issue.check_id,
            severity=issue.severity.value,
            message=issue.message,
            line=issue.line,
            column=issue.column,
            context=issue.context,
            suggestion=issue.suggestion,
            auto_fixable=issue.auto_fixable,
        )
        for issue in issues
    ]


def build_config_summary(config: JobConfig) -> dict[str, Any]:
    """Build a summary of the configuration for display.

    Args:
        config: Parsed JobConfig object

    Returns:
        Dictionary with configuration summary information
    """
    return {
        "name": config.name,
        "total_sheets": config.sheet.total_sheets,
        "backend_type": config.backend.type,
        "validation_count": len(config.validations),
        "notification_count": len(config.notifications),
        "has_dependencies": bool(config.sheet.dependencies),
        "timeout_seconds": getattr(config.sheet, 'timeout_seconds', None),
        "workspace": str(config.workspace),
    }


# ============================================================================
# API Endpoints
# ============================================================================


@router.post("/validate", response_model=ValidateConfigResponse)
async def validate_config(request: ValidateConfigRequest) -> ValidateConfigResponse:
    """Validate a YAML configuration for Mozart jobs.

    Performs comprehensive validation including:
    - YAML syntax checking
    - Pydantic schema validation against JobConfig
    - Extended checks (file existence, Jinja syntax, etc.)
    - Line/column error tracking

    Args:
        request: Validation request with YAML content and options

    Returns:
        Detailed validation results with issues and summary

    Raises:
        HTTPException: 400 if request is malformed
    """
    content = request.content.strip()
    filename = request.filename or "config.yaml"

    # Initialize response fields
    yaml_syntax_valid = False
    schema_valid = False
    config = None
    issues: list[ValidationIssueResponse] = []
    config_summary = None
    error_message = None

    # Phase 1: YAML syntax validation
    _, yaml_error = parse_yaml_safely(content)
    if yaml_error:
        error_message = yaml_error
    else:
        yaml_syntax_valid = True

        # Phase 2: Schema validation
        config, schema_error = validate_schema(content, filename)
        if schema_error:
            error_message = schema_error
            # Add schema errors as issues for consistent UX in validation panel
            issues.append(
                ValidationIssueResponse(
                    check_id="SCHEMA",
                    severity="ERROR",
                    message="Schema validation failed",
                    line=1,  # Schema errors typically relate to the document structure
                    column=None,
                    context=schema_error,
                    suggestion="Check that all required fields are present and have correct types",
                    auto_fixable=False,
                )
            )
        else:
            schema_valid = True

            # Phase 3: Extended validation (only if schema is valid and config is not None)
            if config is not None:
                try:
                    issues = run_extended_validation(
                        config,
                        content,
                        filename,
                        request.workspace_path
                    )

                    # Build config summary for valid configurations
                    config_summary = build_config_summary(config)

                except Exception as e:
                    # Extended validation failed - this is unusual but handle gracefully
                    issues = [
                        ValidationIssueResponse(
                            check_id="V999",
                            severity="ERROR",
                            message=f"Extended validation failed: {e}",
                            line=None,
                            column=None,
                            context=None,
                            auto_fixable=False,
                            suggestion="This may be a bug in Mozart validation system"
                        )
                    ]

    # Count issues by severity
    counts = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for issue in issues:
        severity = issue.severity.upper()  # Convert to uppercase for counting
        if severity in counts:
            counts[severity] += 1

    # Determine if configuration is valid (no ERROR-level issues and basic validation passed)
    valid = yaml_syntax_valid and schema_valid and counts["ERROR"] == 0

    return ValidateConfigResponse(
        valid=valid,
        yaml_syntax_valid=yaml_syntax_valid,
        schema_valid=schema_valid,
        issues=issues,
        counts=counts,
        config_summary=config_summary,
        error_message=error_message,
    )


# ============================================================================
# Template API Endpoints
# ============================================================================


class TemplateResponse(BaseModel):
    """Individual template metadata."""
    name: str = Field(..., description="Template identifier")
    title: str = Field(..., description="Display title")
    filename: str = Field(..., description="YAML filename")
    description: str = Field(..., description="Template description")
    complexity: str = Field(..., description="simple, medium, or complex")
    sheets: int = Field(..., description="Number of sheets")
    category: str = Field(..., description="Template category")
    features: list[str] = Field(default_factory=list, description="Key features")
    variables: list[dict[str, str]] = Field(default_factory=list, description="Template variables")
    content: str = Field(..., description="YAML content")
    estimated_duration: str | None = Field(None, description="Estimated completion time")


class TemplateListResponse(BaseModel):
    """Response for template list."""
    templates: list[TemplateResponse] = Field(..., description="Available templates")
    total: int = Field(..., description="Total template count")
    categories: list[str] = Field(..., description="Available categories")


def analyze_template(name: str, content: str) -> TemplateResponse:
    """Analyze template content and extract metadata.

    Args:
        name: Template name
        content: YAML content

    Returns:
        Template metadata
    """
    try:
        # Parse YAML to extract metadata
        data = yaml.safe_load(content)
        sheets = data.get('sheet', {}).get('total_sheets', 1)

        # Determine complexity based on sheet count
        if sheets == 1:
            complexity = 'simple'
        elif sheets <= 3:
            complexity = 'medium'
        else:
            complexity = 'complex'

        # Extract features from template content
        features = []
        if 'dependencies' in data.get('sheet', {}):
            features.append('Multi-sheet dependencies')
        if data.get('validations'):
            features.append(f"{len(data['validations'])} validation checks")
        if data.get('notifications'):
            features.append('Automated notifications')
        if '{{' in content:
            features.append('Customizable variables')

        # Categorize templates
        category_map = {
            'simple-task': 'workflow',
            'multi-sheet': 'workflow',
            'review-cycle': 'workflow',
            'data-processing': 'data',
            'testing-workflow': 'testing',
            'deployment-pipeline': 'deployment'
        }

        # Extract variables from Jinja templates
        variables = []
        jinja_variable_pattern = r'{{\s*(\w+)(?:\s*\|\s*default\([^)]*\))?\s*}}'
        var_matches = re.findall(jinja_variable_pattern, content)
        for var in set(var_matches):
            variables.append({
                'name': var,
                'description': f'Template variable: {var}'
            })

        sheet_word = "sheet" if sheets == 1 else "sheets"
        category = category_map.get(name, 'general')
        return TemplateResponse(
            name=name,
            title=name.replace('-', ' ').title(),
            filename=TEMPLATE_FILES[name],
            description=f"A {complexity} {category} template with {sheets} {sheet_word}",
            complexity=complexity,
            sheets=sheets,
            category=category,
            features=features,
            variables=variables,
            content=content,
            estimated_duration=f"{sheets * 15}-{sheets * 30} min" if sheets > 1 else "5-15 min"
        )
    except (yaml.YAMLError, KeyError, TypeError, AttributeError, ValueError):
        _logger.debug("Failed to parse template metadata for %s", name, exc_info=True)
        # Fallback metadata if parsing fails
        return TemplateResponse(
            name=name,
            title=name.replace('-', ' ').title(),
            filename=TEMPLATE_FILES.get(name, f"{name}.yaml"),
            description=f"Template: {name}",
            complexity='simple',
            sheets=1,
            category='general',
            content=content,
            estimated_duration="5-15 min"
        )


@router.get("/templates/list", response_model=TemplateListResponse, tags=["Templates"])
async def list_available_templates(
    category: str | None = None,
    complexity: str | None = None,
    search: str | None = None
) -> TemplateListResponse:
    """List all available score templates.

    Args:
        category: Filter by category (workflow, data, testing, deployment)
        complexity: Filter by complexity (simple, medium, complex)
        search: Search in template names and descriptions

    Returns:
        List of available templates with metadata
    """
    templates = []
    categories = set()

    # Get templates from the templates module
    template_dict = list_templates()

    for name in template_dict:
        try:
            template_path = get_template_path(name)
            content = template_path.read_text()
            template = analyze_template(name, content)

            # Apply filters
            if category and template.category != category:
                continue
            if complexity and template.complexity != complexity:
                continue
            if search:
                search_lower = search.lower()
                in_title = search_lower in template.title.lower()
                in_desc = search_lower in template.description.lower()
                if not in_title and not in_desc:
                    continue

            templates.append(template)
            categories.add(template.category)

        except (KeyError, OSError, ValueError, yaml.YAMLError):
            _logger.warning("Failed to load template %s", name, exc_info=True)
            continue

    return TemplateListResponse(
        templates=templates,
        total=len(templates),
        categories=sorted(categories)
    )


@router.get("/templates/{template_name}", response_model=TemplateResponse, tags=["Templates"])
async def get_template(template_name: str) -> TemplateResponse:
    """Get detailed information about a specific template.

    Args:
        template_name: Template identifier

    Returns:
        Template details

    Raises:
        HTTPException: 404 if template not found
    """
    try:
        template_path = get_template_path(template_name)
        content = template_path.read_text()
        return analyze_template(template_name, content)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Template '{template_name}' not found"
        ) from None
    except (OSError, ValueError, yaml.YAMLError):
        _logger.warning("Failed to load template %s", template_name, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to load template"
        ) from None


@router.get("/templates/{template_name}/download", tags=["Templates"])
async def download_template(template_name: str) -> PlainTextResponse:
    """Download a template as a YAML file.

    Args:
        template_name: Template identifier

    Returns:
        YAML file download

    Raises:
        HTTPException: 404 if template not found
    """
    try:
        template_path = get_template_path(template_name)
        content = template_path.read_text()

        return PlainTextResponse(
            content,
            media_type="application/x-yaml",
            headers={
                "Content-Disposition": f"attachment; filename={TEMPLATE_FILES[template_name]}"
            }
        )
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Template '{template_name}' not found"
        ) from None
    except OSError:
        _logger.warning("Failed to download template %s", template_name, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to download template"
        ) from None


@router.post("/templates/{template_name}/use", tags=["Templates"])
async def use_template(template_name: str) -> RedirectResponse:
    """Use a template by redirecting to editor with template content.

    Args:
        template_name: Template identifier

    Returns:
        Redirect to score editor

    Raises:
        HTTPException: 404 if template not found
    """
    try:
        # Verify template exists
        get_template_path(template_name)

        # Redirect to editor with template parameter
        return RedirectResponse(
            url=f"/editor?template={template_name}",
            status_code=302
        )
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Template '{template_name}' not found"
        ) from None
    except OSError:
        _logger.warning("Failed to use template %s", template_name, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to use template"
        ) from None


