"""Marianne MCP resources for score and conductor-facing configuration access.

This module implements MCP resources that expose score schemas, examples, and
conductor records as readable content. Resources provide context and reference
material for AI agents working with Marianne.

Resources are organized by category:
- ConfigResources: Access to score schemas and examples
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from marianne.core.config import JobConfig
from marianne.core.constants import SHEET_NUM_KEY
from marianne.state.base import StateBackend

logger = logging.getLogger(__name__)

# Content type constant to avoid magic string repetition
_CONTENT_TYPE_JSON = "application/json"


class ConfigResources:
    """Marianne score configuration resources.

    Provides access to score configuration schemas, examples, and documentation
    as MCP resources. These resources help AI agents understand Marianne's score
    format and available instrument options.
    """

    def __init__(
        self, state_backend: StateBackend | None = None, workspace_root: Path | None = None
    ) -> None:
        # Base project directory (assuming we're in src/marianne/mcp/)
        self.project_root = Path(__file__).parent.parent.parent.parent
        self.state_backend = state_backend
        self.workspace_root = workspace_root or Path.cwd()

    async def list_resources(self) -> list[dict[str, Any]]:
        """List all configuration resources."""
        resources = [
            {
                "uri": "config://schema",
                "name": "Marianne Score Schema",
                "description": "Complete JSON schema for Marianne score YAML files",
                "mimeType": _CONTENT_TYPE_JSON
            },
            {
                "uri": "config://example",
                "name": "Marianne Score Example",
                "description": "Example Marianne score YAML with current syntax",
                "mimeType": "text/yaml"
            },
            {
                "uri": "config://instrument-options",
                "name": "Instrument Options",
                "description": "Available instrument profiles and their configuration options",
                "mimeType": _CONTENT_TYPE_JSON
            },
            {
                "uri": "config://backend-options",
                "name": "Instrument Options (compatibility URI)",
                "description": (
                    "Compatibility alias for config://instrument-options; "
                    "Marianne score YAML uses instrument profiles, not backend blocks"
                ),
                "mimeType": _CONTENT_TYPE_JSON
            },
            {
                "uri": "config://validation-types",
                "name": "Validation Types Reference",
                "description": "Available validation types and their parameters",
                "mimeType": _CONTENT_TYPE_JSON
            },
            {
                "uri": "config://learning-options",
                "name": "Learning Configuration Options",
                "description": "Learning system configuration parameters and patterns",
                "mimeType": _CONTENT_TYPE_JSON
            },
            # Job management resources
            {
                "uri": "marianne://jobs",
                "name": "Submitted Scores Overview",
                "description": (
                    "List of conductor records for submitted scores with status and metadata"
                ),
                "mimeType": _CONTENT_TYPE_JSON
            },
            {
                "uri": "marianne://templates",
                "name": "Marianne Score Templates",
                "description": "Collection of Marianne score YAML templates",
                "mimeType": _CONTENT_TYPE_JSON
            }
        ]

        # Dynamic job detail resources - only available if we have state backend
        if self.state_backend:
            resources.append({
                "uri": "marianne://jobs/{job_id}",
                "name": "Submitted Score Details (Template)",
                "description": (
                    "Detailed conductor record for a submitted score; "
                    "job_id is the runtime identifier"
                ),
                "mimeType": _CONTENT_TYPE_JSON
            })

        return resources

    # URI dispatch table mapping static URIs to handler methods
    _URI_HANDLERS: dict[str, str] = {
        "config://schema": "_get_config_schema",
        "config://example": "_get_config_example",
        "config://instrument-options": "_get_instrument_options",
        "config://backend-options": "_get_instrument_options",
        "config://validation-types": "_get_validation_types",
        "config://learning-options": "_get_learning_options",
        "marianne://jobs": "_get_jobs_overview",
        "marianne://templates": "_get_job_templates",
    }

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read a configuration resource by URI."""
        try:
            handler_name = self._URI_HANDLERS.get(uri)
            if handler_name:
                result: dict[str, Any] = await getattr(self, handler_name)()
                return result

            if uri.startswith("marianne://jobs/"):
                job_id = uri.replace("marianne://jobs/", "")
                return await self._get_job_details(job_id)

            raise ValueError(f"Unknown resource URI: {uri}")

        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            logger.warning("Error reading resource %s: %s", uri, e)
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": f"Error reading resource: {str(e)}"
                    }
                ]
            }

    async def _get_config_schema(self) -> dict[str, Any]:
        """Generate JSON schema for Marianne score configuration from Pydantic models.

        Uses JobConfig.model_json_schema() to generate a schema that stays
        in sync with the actual Pydantic models, avoiding manual drift.
        """
        return self._mcp_json_content("config://schema", JobConfig.model_json_schema())

    async def _get_config_example(self) -> dict[str, Any]:
        """Get example Marianne score configuration."""
        example_content = """# Marianne Score Example

name: example-review
description: Example Marianne score configuration

# The top-level ``instrument:`` key resolves through the instrument
# registry and works for any registered instrument profile
# (claude-code, anthropic_api, gemini-cli, etc.).
instrument: claude-code
instrument_config:
  timeout_seconds: 300

sheet:
  size: 1
  total_items: 2

prompt:
  template: |
    Sheet {{ sheet_num }}:
    Analyze the current directory and write a short report to
    analysis-summary.md.

validations:
  - type: file_exists
    description: "Analysis output written to file"
    path: "analysis-summary.md"

workspace: "./workspace"
learning:
  enabled: true
  use_global_patterns: true

notifications:
  - type: desktop
    title: "Marianne Score Complete"
"""

        return {
            "contents": [
                {
                    "uri": "config://example",
                    "mimeType": "text/yaml",
                    "text": example_content
                }
            ]
        }

    async def _get_instrument_options(self) -> dict[str, Any]:
        """Get instrument configuration options.

        Phase 5: registry-driven. Reads the live ``InstrumentRegistry``
        (populated by ``register_native_instruments()`` plus any
        user/project profiles) instead of hardcoding the legacy 4
        backend options. Each registered profile is surfaced with its
        kind, capabilities, default model, timeout, and model capacity
        list so MCP clients see an accurate, extensible picture rather
        than a stale backend snapshot.
        """
        from marianne.instruments.registry import (
            InstrumentRegistry,
            register_native_instruments,
        )

        # Build a local registry view. We avoid holding a registry on the
        # ConfigResources instance because MCP resources are read-only
        # snapshots — each call reflects the current native bridge state.
        registry = InstrumentRegistry()
        register_native_instruments(registry)

        available: dict[str, Any] = {}
        for profile in registry.list_all():
            available[profile.name] = {
                "display_name": profile.display_name,
                "description": profile.description,
                "kind": profile.kind,
                "capabilities": sorted(profile.capabilities),
                "default_model": profile.default_model,
                "default_timeout_seconds": profile.default_timeout_seconds,
                "models": [
                    {
                        "name": m.name,
                        "context_window": m.context_window,
                        "cost_per_1k_input": m.cost_per_1k_input,
                        "cost_per_1k_output": m.cost_per_1k_output,
                        "max_output_tokens": m.max_output_tokens,
                    }
                    for m in profile.models
                ],
            }

        instrument_options = {
            "available_instruments": available,
            "compatibility_note": (
                "Legacy backend blocks were removed from score YAML. "
                "Use the top-level instrument field and instrument_config instead."
            ),
        }
        return self._mcp_json_content("config://instrument-options", instrument_options)

    async def _get_validation_types(self) -> dict[str, Any]:
        """Get validation types reference."""
        validation_types = {
            "available_validation_types": {
                "file_exists": {
                    "description": "Check if a file exists at the specified path",
                    "parameters": {
                        "path": "Required. File path to check (relative to workspace)"
                    },
                    "example": {
                        "type": "file_exists",
                        "description": "Output file was created",
                        "path": "results.txt"
                    }
                },
                "file_modified": {
                    "description": "Check if a file was modified during the run",
                    "parameters": {
                        "path": "Required. File path to check (relative to workspace)"
                    },
                    "example": {
                        "type": "file_modified",
                        "description": "Report was refreshed",
                        "path": "report.md"
                    }
                },
                "content_contains": {
                    "description": "Check that a file contains expected text",
                    "parameters": {
                        "path": "Required. File path to read",
                        "pattern": "Required. Text to find"
                    },
                    "example": {
                        "type": "content_contains",
                        "description": "Report names the verdict",
                        "path": "report.md",
                        "pattern": "Verdict"
                    }
                },
                "content_regex": {
                    "description": "Check that a file matches a regular expression",
                    "parameters": {
                        "path": "Required. File path to read",
                        "pattern": "Required. Regular expression pattern"
                    },
                    "example": {
                        "type": "content_regex",
                        "description": "Report includes a status",
                        "path": "report.md",
                        "pattern": "Status: (pass|fail)"
                    }
                },
                "command_succeeds": {
                    "description": "Run a command and require exit code 0",
                    "parameters": {
                        "command": "Required. Shell command to execute",
                        "working_directory": "Optional. Directory to run from"
                    },
                    "example": {
                        "type": "command_succeeds",
                        "description": "Tests pass",
                        "command": "pytest -q"
                    }
                },
                "path_in_scope": {
                    "description": "Check that a path remains inside an allowed root",
                    "parameters": {
                        "path": "Required. Path to check",
                        "path_scope": "Optional. Allowed root, defaults to workspace"
                    },
                    "example": {
                        "type": "path_in_scope",
                        "description": "Output stays in the workspace",
                        "path": "report.md",
                        "path_scope": "{workspace}"
                    }
                },
                "field_match": {
                    "description": "Check a JSON/YAML field value",
                    "parameters": {
                        "path": "Required. JSON or YAML file path",
                        "field_path": "Required. Dot/bracket field path",
                        "expected_value": "Optional. Literal value to compare"
                    },
                    "example": {
                        "type": "field_match",
                        "description": "Summary status is pass",
                        "path": "summary.json",
                        "field_path": "status",
                        "expected_value": "pass"
                    }
                },
                "file_sha256": {
                    "description": "Check a file's SHA-256 digest",
                    "parameters": {
                        "path": "Required. File path",
                        "sha256": "Required. Expected digest"
                    },
                    "example": {
                        "type": "file_sha256",
                        "description": "Pinned artifact digest matches",
                        "path": "artifact.bin",
                        "sha256": "<expected-sha256>"
                    }
                },
                "csv_unique_key": {
                    "description": "Check that a CSV column has unique values",
                    "parameters": {
                        "path": "Required. CSV file path",
                        "key_field": "Required. Column that must be unique"
                    },
                    "example": {
                        "type": "csv_unique_key",
                        "description": "No duplicate ids",
                        "path": "rows.csv",
                        "key_field": "id"
                    }
                }
            }
        }

        return self._mcp_json_content("config://validation-types", validation_types)

    async def _get_learning_options(self) -> dict[str, Any]:
        """Get learning configuration options."""
        learning_options = {
            "learning_system": {
                "description": "Marianne's adaptive learning system configuration",
                "options": {
                    "enabled": {
                        "type": "boolean",
                        "default": True,
                        "description": "Enable pattern learning and adaptation"
                    },
                    "use_global_patterns": {
                        "type": "boolean",
                        "default": True,
                        "description": "Query and apply patterns from the global learning store"
                    },
                    "escalation": {
                        "type": "boolean",
                        "default": False,
                        "description": "Enable escalation to more powerful models"
                    },
                    "global_learning": {
                        "type": "boolean",
                        "default": True,
                        "description": "Participate in global learning across submitted scores"
                    },
                    "pattern_trust_threshold": {
                        "type": "number",
                        "default": 0.7,
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Minimum trust score for applying patterns"
                    }
                }
            },
            "pattern_types": [
                "error_resolution",
                "optimization",
                "validation_improvement",
                "retry_strategy",
                "timeout_adjustment",
                "dependency_ordering"
            ]
        }

        return self._mcp_json_content("config://learning-options", learning_options)

    # Mapping from job status value to summary counter key
    _STATUS_COUNTER_KEYS: dict[str, str] = {
        "running": "running_jobs",
        "completed": "completed_jobs",
        "failed": "failed_jobs",
        "paused": "paused_jobs",
    }

    async def _get_jobs_overview(self) -> dict[str, Any]:
        """Get overview of submitted Marianne score runs."""
        if not self.state_backend:
            return self._mcp_json_content("marianne://jobs", {
                "error": "Submitted score overview requires state backend initialization",
                "note": "Configure MCP server with workspace_root to enable score-run listing",
            })

        jobs_overview: dict[str, Any] = {
            "jobs": [],
            "summary": {
                "total_jobs": 0,
                "running_jobs": 0,
                "completed_jobs": 0,
                "failed_jobs": 0,
                "paused_jobs": 0
            },
            "last_updated": datetime.now().isoformat()
        }

        try:
            for state_file in self.workspace_root.glob("*.json"):
                if state_file.stem == "global_learning":
                    continue
                job_info = await self._load_job_summary(state_file.stem)
                if job_info is None:
                    continue
                jobs_overview["jobs"].append(job_info)
                jobs_overview["summary"]["total_jobs"] += 1
                counter_key = self._STATUS_COUNTER_KEYS.get(job_info["status"])
                if counter_key:
                    jobs_overview["summary"][counter_key] += 1
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            jobs_overview["error"] = f"Error scanning jobs: {str(e)}"

        return self._mcp_json_content("marianne://jobs", jobs_overview)

    async def _load_job_summary(self, job_id: str) -> dict[str, Any] | None:
        """Load a single job's summary from state backend.

        Returns None if the job cannot be loaded (invalid file, etc.).
        Must only be called when self.state_backend is not None.
        """
        assert self.state_backend is not None  # guaranteed by caller
        try:
            state = await self.state_backend.load(job_id)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            logger.warning("Skipping invalid state file %s: %s", job_id, e)
            return None

        if not state:
            return None

        return {
            "job_id": job_id,
            "job_name": state.job_name,
            "status": state.status.value,
            "started_at": state.started_at.isoformat() if state.started_at else None,
            "completed_at": state.completed_at.isoformat() if state.completed_at else None,
            "total_sheets": len(state.sheets),
            "completed_sheets": len([s for s in state.sheets.values()
                                   if s.status.value == "completed"]),
            "error_message": getattr(state, 'error_message', None)
        }

    @staticmethod
    def _mcp_json_content(uri: str, data: dict[str, Any]) -> dict[str, Any]:
        """Wrap data in MCP content response format."""
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": _CONTENT_TYPE_JSON,
                    "text": json.dumps(data, indent=2),
                }
            ]
        }

    @staticmethod
    def _count_sheets_by_status(sheets: dict[Any, Any]) -> dict[str, int]:
        """Count sheets by their status value."""
        counts: dict[str, int] = {}
        for sheet in sheets.values():
            status = sheet.status.value
            counts[status] = counts.get(status, 0) + 1
        return counts

    async def _get_job_details(self, job_id: str) -> dict[str, Any]:
        """Get detailed information about a specific job."""
        uri = f"marianne://jobs/{job_id}"

        if not self.state_backend:
            return self._mcp_json_content(uri, {
                "error": "Job details require state backend initialization"
            })

        try:
            state = await self.state_backend.load(job_id)
            if not state:
                raise FileNotFoundError(f"Job not found: {job_id}")

            status_counts = self._count_sheets_by_status(state.sheets)

            job_details: dict[str, Any] = {
                "job_id": job_id,
                "job_name": state.job_name,
                "status": state.status.value,
                "started_at": state.started_at.isoformat() if state.started_at else None,
                "completed_at": state.completed_at.isoformat() if state.completed_at else None,
                "last_updated": state.updated_at.isoformat() if state.updated_at else None,
                "error_message": state.error_message,
                "total_sheets": len(state.sheets),
                "sheets": {},
                "configuration": {
                    "workspace": str(state.workspace) if hasattr(state, 'workspace') else None,
                    "instruments_used": list(getattr(state, 'instruments_used', []) or []),
                },
                "progress": {
                    "completed_sheets": status_counts.get("completed", 0),
                    "failed_sheets": status_counts.get("failed", 0),
                    "running_sheets": status_counts.get("running", 0),
                    "pending_sheets": status_counts.get("pending", 0),
                },
            }

            for sheet_num, sheet in state.sheets.items():
                job_details["sheets"][str(sheet_num)] = {
                    SHEET_NUM_KEY: sheet.sheet_num,
                    "status": sheet.status.value,
                    "started_at": sheet.started_at.isoformat() if sheet.started_at else None,
                    "completed_at": sheet.completed_at.isoformat() if sheet.completed_at else None,
                    "attempt_count": sheet.attempt_count,
                    "error_message": sheet.error_message,
                    "validation_passed": getattr(sheet, 'validation_passed', None),
                    "output_size": len(sheet.stdout_tail) if sheet.stdout_tail else 0,
                }

            return self._mcp_json_content(uri, job_details)

        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            return self._mcp_json_content(uri, {
                "error": f"Error loading job details: {str(e)}",
                "job_id": job_id,
            })

    async def _get_job_templates(self) -> dict[str, Any]:
        """Get collection of Marianne score templates."""
        templates = {
            "templates": {
                "code-analysis": _build_code_analysis_template(),
                "test-generation": _build_test_generation_template(),
                "documentation": _build_documentation_template(),
                "refactoring": _build_refactoring_template(),
            },
            "usage": _build_template_usage_guide(),
        }

        return self._mcp_json_content("marianne://templates", templates)


def _build_code_analysis_template() -> dict[str, Any]:
    """Build code analysis score template."""
    return {
        "name": "Code Analysis Template",
        "description": "Template for analyzing codebases and generating documentation",
        "use_cases": ["code review", "documentation generation", "architecture analysis"],
        "config": {
            "name": "code-analysis-{timestamp}",
            "description": "Analyze codebase structure and patterns",
            "instrument": "claude-code",
            "instrument_config": {"timeout_seconds": 300},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {
                "template": (
                    "Scan the current directory and write analysis-summary.md with:\n"
                    "1. Project structure and key files\n"
                    "2. Programming languages and frameworks used\n"
                    "3. Main functionality and purpose\n"
                )
            },
            "validations": [
                {
                    "type": "file_exists",
                    "description": "Analysis report was written",
                    "path": "analysis-summary.md",
                },
            ],
        },
    }


def _build_test_generation_template() -> dict[str, Any]:
    """Build test generation score template."""
    return {
        "name": "Test Generation Template",
        "description": "Template for generating comprehensive tests for existing code",
        "use_cases": ["test coverage", "quality assurance", "regression testing"],
        "config": {
            "name": "test-generation-{timestamp}",
            "description": "Generate comprehensive tests for codebase",
            "instrument": "claude-code",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {
                "template": (
                    "Identify components that need test coverage and write "
                    "tests/generated/test_plan.md with priorities, edge cases, "
                    "and commands to run."
                )
            },
            "validations": [
                {
                    "type": "file_exists",
                    "description": "Test plan was written",
                    "path": "tests/generated/test_plan.md",
                },
            ],
        },
    }


def _build_documentation_template() -> dict[str, Any]:
    """Build documentation generation score template."""
    return {
        "name": "Documentation Template",
        "description": "Template for generating project documentation",
        "use_cases": ["API documentation", "user guides", "README creation"],
        "config": {
            "name": "documentation-{timestamp}",
            "description": "Generate comprehensive project documentation",
            "instrument": "claude-code",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {
                "template": (
                    "Create README.md with setup instructions, usage examples, "
                    "and an API overview for this project."
                )
            },
            "validations": [
                {
                    "type": "file_exists",
                    "description": "README.md created",
                    "path": "README.md",
                },
                {
                    "type": "content_regex",
                    "description": "README contains required sections",
                    "path": "README.md",
                    "pattern": "Installation|Usage|API",
                },
            ],
        },
    }


def _build_refactoring_template() -> dict[str, Any]:
    """Build code refactoring score template."""
    return {
        "name": "Code Refactoring Template",
        "description": "Template for systematic code refactoring and improvement",
        "use_cases": ["code cleanup", "performance optimization", "modernization"],
        "config": {
            "name": "refactoring-{timestamp}",
            "description": "Systematic code refactoring and improvement",
            "instrument": "claude-code",
            "learning": {"enabled": True, "use_global_patterns": True},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {
                "template": (
                    "Identify safe refactoring opportunities, apply the smallest "
                    "useful changes, and write refactor-summary.md with the "
                    "changed files and verification commands."
                )
            },
            "validations": [
                {
                    "type": "file_exists",
                    "description": "Refactor summary was written",
                    "path": "refactor-summary.md",
                },
                {
                    "type": "command_succeeds",
                    "description": "Python files still compile",
                    "command": "python -m compileall -q .",
                },
            ],
        },
    }


def _build_template_usage_guide() -> dict[str, Any]:
    """Build usage guidance for score templates."""
    return {
        "description": (
            "Marianne score templates provide starting points"
            " for common development tasks"
        ),
        "how_to_use": [
            "Copy the desired score configuration",
            "Replace {timestamp} placeholders with actual values",
            "Modify prompts and validation rules for your specific needs",
            "Add or remove sheets based on your requirements",
            "Configure instrument and instrument_config for your environment",
        ],
        "customization_tips": [
            "Adjust timeout_seconds based on expected task complexity",
            "Add sheet dependencies to ensure proper execution order",
            "Use content_regex validation for content verification",
            "Use file_exists validation for output verification",
            "Enable learning to improve performance over time",
        ],
    }
