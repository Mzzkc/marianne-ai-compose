"""Marianne MCP Resources - Resource implementations for Mzt configuration access.

This module implements MCP resources that expose Mzt configuration and
documentation as readable content. Resources provide context and reference
material for AI agents working with Marianne.

Resources are organized by category:
- ConfigResources: Access to Mzt configuration schemas and examples
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from marianne.core.config import JobConfig
from marianne.state.base import StateBackend

logger = logging.getLogger(__name__)

# Content type constant to avoid magic string repetition
_CONTENT_TYPE_JSON = "application/json"


class ConfigResources:
    """Mzt configuration resources.

    Provides access to Mzt configuration schemas, examples, and documentation
    as MCP resources. These resources help AI agents understand Marianne's
    configuration format and available options.
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
                "name": "Mzt configuration Schema",
                "description": "Complete JSON schema for Marianne job configuration files",
                "mimeType": _CONTENT_TYPE_JSON
            },
            {
                "uri": "config://example",
                "name": "Mzt configuration Example",
                "description": "Example Marianne job configuration with common patterns",
                "mimeType": "text/yaml"
            },
            {
                "uri": "config://backend-options",
                "name": "Backend Configuration Options",
                "description": "Available backend types and their configuration options",
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
                "name": "Marianne Jobs Overview",
                "description": "List of all Marianne jobs with status and metadata",
                "mimeType": _CONTENT_TYPE_JSON
            },
            {
                "uri": "marianne://templates",
                "name": "Marianne Job Templates",
                "description": "Collection of Marianne job configuration templates",
                "mimeType": _CONTENT_TYPE_JSON
            }
        ]

        # Dynamic job detail resources - only available if we have state backend
        if self.state_backend:
            resources.append({
                "uri": "marianne://jobs/{job_id}",
                "name": "Marianne Job Details (Template)",
                "description": "Detailed information about a specific Marianne job",
                "mimeType": _CONTENT_TYPE_JSON
            })

        return resources

    # URI dispatch table mapping static URIs to handler methods
    _URI_HANDLERS: dict[str, str] = {
        "config://schema": "_get_config_schema",
        "config://example": "_get_config_example",
        "config://backend-options": "_get_backend_options",
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
        """Generate JSON schema for Mzt configuration from Pydantic models.

        Uses JobConfig.model_json_schema() to generate a schema that stays
        in sync with the actual Pydantic models, avoiding manual drift.
        """
        return self._mcp_json_content("config://schema", JobConfig.model_json_schema())

    async def _get_config_example(self) -> dict[str, Any]:
        """Get example Mzt configuration."""
        example_content = """# Marianne Job Configuration Example

job_id: example-review
description: Example Marianne job configuration

backend:
  backend_type: claude_cli
  disable_mcp: true  # For faster execution
  timeout_seconds: 300

sheets:
  - name: analyze-code
    prompt: |
      Analyze the code in the current directory and provide a summary of:
      1. Main functionality
      2. Key patterns and architecture
      3. Potential improvements

    timeout_seconds: 180
    max_retries: 3

    validation:
      - type: regex_match
        description: "Output contains analysis sections"
        pattern: "Main functionality|Key patterns|Potential improvements"

      - type: file_exists
        description: "Analysis output written to file"
        path: "analysis-summary.md"

  - name: create-documentation
    prompt: |
      Based on the analysis from the previous sheet, create comprehensive
      documentation for this codebase including:
      1. Setup instructions
      2. Usage examples
      3. API reference

      Previous analysis: {{ sheets.0.output }}

    dependencies: ["analyze-code"]
    timeout_seconds: 240

    validation:
      - type: file_exists
        description: "Documentation created"
        path: "README.md"

      - type: regex_match
        description: "Documentation contains required sections"
        pattern: "Setup|Usage|API"

# Global configuration
workspace: "./workspace"
learning:
  enabled: true
  pattern_detection: true

notifications:
  - type: desktop
    title: "Marianne Job Complete"
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

    async def _get_backend_options(self) -> dict[str, Any]:
        """Get backend configuration options."""
        backend_options = {
            "available_backends": {
                "claude_cli": {
                    "description": "Claude CLI backend using subprocess calls",
                    "options": {
                        "disable_mcp": {
                            "type": "boolean",
                            "default": True,
                            "description": "Disable MCP servers for faster execution"
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "default": 300,
                            "description": "Timeout for individual requests"
                        },
                        "cli_extra_args": {
                            "type": "array",
                            "description": "Additional CLI arguments to pass"
                        }
                    }
                },
                "anthropic_api": {
                    "description": "Direct Anthropic API backend",
                    "options": {
                        "api_key": {
                            "type": "string",
                            "description": "Anthropic API key (or use ANTHROPIC_API_KEY env var)"
                        },
                        "model": {
                            "type": "string",
                            "default": "claude-3-5-sonnet-20241022",
                            "description": "Model to use for requests"
                        },
                        "max_tokens": {
                            "type": "integer",
                            "default": 8192,
                            "description": "Maximum tokens per response"
                        }
                    }
                }
            }
        }

        return self._mcp_json_content("config://backend-options", backend_options)

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
                "regex_match": {
                    "description": "Check if output matches a regular expression",
                    "parameters": {
                        "pattern": "Required. Regular expression pattern to match",
                        "flags": "Optional. Regex flags (i for case-insensitive, etc.)"
                    },
                    "example": {
                        "type": "regex_match",
                        "description": "Output contains success message",
                        "pattern": "Success|Complete|Done"
                    }
                },
                "json_schema": {
                    "description": "Validate output against a JSON schema",
                    "parameters": {
                        "schema": "Required. JSON schema to validate against"
                    },
                    "example": {
                        "type": "json_schema",
                        "description": "Output is valid JSON with required fields",
                        "schema": {
                            "type": "object",
                            "required": ["status", "result"],
                            "properties": {
                                "status": {"type": "string"},
                                "result": {"type": "string"}
                            }
                        }
                    }
                },
                "custom": {
                    "description": "Run custom validation command",
                    "parameters": {
                        "command": "Required. Command to execute for validation",
                        "expected_exit_code": "Optional. Expected exit code (default: 0)"
                    },
                    "example": {
                        "type": "custom",
                        "description": "Custom script validates results",
                        "command": "python validate_results.py"
                    }
                },
                "llm_judge": {
                    "description": "Use LLM to judge output quality",
                    "parameters": {
                        "criteria": "Required. Criteria for the LLM to evaluate",
                        "model": "Optional. Model to use for judging"
                    },
                    "example": {
                        "type": "llm_judge",
                        "description": "Output provides clear, helpful analysis",
                        "criteria": (
                            "The output should be well-structured,"
                            " informative, and directly address"
                            " the prompt requirements"
                        )
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
                    "pattern_detection": {
                        "type": "boolean",
                        "default": True,
                        "description": "Enable automatic pattern detection"
                    },
                    "escalation": {
                        "type": "boolean",
                        "default": False,
                        "description": "Enable escalation to more powerful models"
                    },
                    "global_learning": {
                        "type": "boolean",
                        "default": True,
                        "description": "Participate in global learning across jobs"
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
        """Get overview of all Marianne jobs."""
        if not self.state_backend:
            return self._mcp_json_content("marianne://jobs", {
                "error": "Jobs overview requires state backend initialization",
                "note": "Configure MCP server with workspace_root to enable job listing",
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
                    "backend_type": getattr(state, 'backend_type', 'unknown'),
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
                    "sheet_num": sheet.sheet_num,
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
        """Get collection of Marianne job configuration templates."""
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
    """Build code analysis job template."""
    return {
        "name": "Code Analysis Template",
        "description": "Template for analyzing codebases and generating documentation",
        "use_cases": ["code review", "documentation generation", "architecture analysis"],
        "config": {
            "job_id": "code-analysis-{timestamp}",
            "description": "Analyze codebase structure and patterns",
            "backend": {
                "backend_type": "claude_cli",
                "disable_mcp": True,
                "timeout_seconds": 300,
            },
            "sheets": [
                {
                    "name": "scan-codebase",
                    "prompt": (
                        "Scan the current directory and provide an overview of:\n"
                        "1. Project structure and key files\n"
                        "2. Programming languages and frameworks used\n"
                        "3. Main functionality and purpose\n\n"
                        "Focus on understanding the codebase architecture."
                    ),
                    "timeout_seconds": 180,
                    "max_retries": 2,
                    "validation": [
                        {
                            "type": "regex_match",
                            "description": "Output contains project structure analysis",
                            "pattern": "structure|files|directories",
                        }
                    ],
                },
                {
                    "name": "analyze-patterns",
                    "prompt": (
                        "Based on the codebase scan, analyze:\n"
                        "1. Architectural patterns and design principles\n"
                        "2. Code quality and potential improvements\n"
                        "3. Documentation gaps\n\n"
                        "Previous scan: {{ sheets.0.output }}"
                    ),
                    "dependencies": ["scan-codebase"],
                    "timeout_seconds": 240,
                    "validation": [
                        {
                            "type": "regex_match",
                            "description": "Analysis contains patterns and improvements",
                            "pattern": "patterns|quality|improvements",
                        }
                    ],
                },
            ],
        },
    }


def _build_test_generation_template() -> dict[str, Any]:
    """Build test generation job template."""
    return {
        "name": "Test Generation Template",
        "description": "Template for generating comprehensive tests for existing code",
        "use_cases": ["test coverage", "quality assurance", "regression testing"],
        "config": {
            "job_id": "test-generation-{timestamp}",
            "description": "Generate comprehensive tests for codebase",
            "backend": {"backend_type": "claude_cli", "disable_mcp": True},
            "sheets": [
                {
                    "name": "identify-testable-units",
                    "prompt": (
                        "Identify functions, classes, and modules that need test coverage:\n"
                        "1. List main functions and classes\n"
                        "2. Identify current test coverage gaps\n"
                        "3. Prioritize by importance and complexity"
                    ),
                    "timeout_seconds": 180,
                    "validation": [
                        {
                            "type": "regex_match",
                            "description": "Lists testable units",
                            "pattern": "functions|classes|coverage|test",
                        }
                    ],
                },
                {
                    "name": "generate-unit-tests",
                    "prompt": (
                        "Generate unit tests for the identified components:\n"
                        "1. Create comprehensive test cases\n"
                        "2. Include edge cases and error conditions\n"
                        "3. Follow project testing conventions\n\n"
                        "Components to test: {{ sheets.0.output }}"
                    ),
                    "dependencies": ["identify-testable-units"],
                    "timeout_seconds": 300,
                    "validation": [
                        {
                            "type": "file_exists",
                            "description": "Test files created",
                            "path": "tests/",
                        }
                    ],
                },
            ],
        },
    }


def _build_documentation_template() -> dict[str, Any]:
    """Build documentation generation job template."""
    return {
        "name": "Documentation Template",
        "description": "Template for generating project documentation",
        "use_cases": ["API documentation", "user guides", "README creation"],
        "config": {
            "job_id": "documentation-{timestamp}",
            "description": "Generate comprehensive project documentation",
            "backend": {"backend_type": "claude_cli", "disable_mcp": True},
            "sheets": [
                {
                    "name": "create-readme",
                    "prompt": (
                        "Create a comprehensive README.md file including:\n"
                        "1. Project description and purpose\n"
                        "2. Installation instructions\n"
                        "3. Usage examples\n"
                        "4. Contributing guidelines"
                    ),
                    "timeout_seconds": 240,
                    "validation": [
                        {
                            "type": "file_exists",
                            "description": "README.md created",
                            "path": "README.md",
                        },
                        {
                            "type": "regex_match",
                            "description": "README contains required sections",
                            "pattern": "Installation|Usage|Contributing",
                        },
                    ],
                },
                {
                    "name": "api-documentation",
                    "prompt": (
                        "Generate API documentation:\n"
                        "1. Document all public functions and classes\n"
                        "2. Include parameter descriptions and examples\n"
                        "3. Add usage examples for key features"
                    ),
                    "timeout_seconds": 300,
                    "validation": [
                        {
                            "type": "file_exists",
                            "description": "API documentation created",
                            "path": "docs/",
                        }
                    ],
                },
            ],
        },
    }


def _build_refactoring_template() -> dict[str, Any]:
    """Build code refactoring job template."""
    return {
        "name": "Code Refactoring Template",
        "description": "Template for systematic code refactoring and improvement",
        "use_cases": ["code cleanup", "performance optimization", "modernization"],
        "config": {
            "job_id": "refactoring-{timestamp}",
            "description": "Systematic code refactoring and improvement",
            "backend": {"backend_type": "claude_cli", "disable_mcp": True},
            "learning": {"enabled": True, "pattern_detection": True},
            "sheets": [
                {
                    "name": "identify-improvements",
                    "prompt": (
                        "Identify code improvements:\n"
                        "1. Find code duplication and redundancy\n"
                        "2. Identify performance bottlenecks\n"
                        "3. Look for outdated patterns or deprecated usage\n"
                        "4. Suggest modernization opportunities"
                    ),
                    "timeout_seconds": 180,
                    "validation": [
                        {
                            "type": "regex_match",
                            "description": "Identifies specific improvements",
                            "pattern": "duplication|performance|deprecated|improvements",
                        }
                    ],
                },
                {
                    "name": "implement-refactoring",
                    "prompt": (
                        "Implement the identified improvements:\n"
                        "1. Refactor duplicated code into reusable functions\n"
                        "2. Optimize performance bottlenecks\n"
                        "3. Update deprecated usage\n"
                        "4. Ensure backward compatibility\n\n"
                        "Improvements to implement: {{ sheets.0.output }}"
                    ),
                    "dependencies": ["identify-improvements"],
                    "timeout_seconds": 400,
                    "max_retries": 2,
                    "validation": [
                        {
                            "type": "custom",
                            "description": "Code still compiles after refactoring",
                            "command": "python -m py_compile **/*.py",
                        }
                    ],
                },
            ],
        },
    }


def _build_template_usage_guide() -> dict[str, Any]:
    """Build usage guidance for job templates."""
    return {
        "description": (
            "Marianne job templates provide starting points"
            " for common development tasks"
        ),
        "how_to_use": [
            "Copy the desired template configuration",
            "Replace {timestamp} placeholders with actual values",
            "Modify prompts and validation rules for your specific needs",
            "Add or remove sheets based on your requirements",
            "Configure backend settings for your environment",
        ],
        "customization_tips": [
            "Adjust timeout_seconds based on expected task complexity",
            "Add sheet dependencies to ensure proper execution order",
            "Use regex_match validation for content verification",
            "Use file_exists validation for output verification",
            "Enable learning to improve performance over time",
        ],
    }

