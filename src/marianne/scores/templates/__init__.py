"""Marianne score templates.

Pre-built score configurations for common use cases.
"""

from pathlib import Path

# Template directory path
TEMPLATES_DIR = Path(__file__).parent

# Available template files
TEMPLATE_FILES = {
    "simple-task": "simple-task.yaml",
    "multi-sheet": "multi-sheet.yaml",
    "review-cycle": "review-cycle.yaml",
    "data-processing": "data-processing.yaml",
    "testing-workflow": "testing-workflow.yaml",
    "deployment-pipeline": "deployment-pipeline.yaml",
}

def get_template_path(template_name: str) -> Path:
    """Get the full path to a template file.

    Args:
        template_name: Template name (without .yaml extension)

    Returns:
        Path to template file

    Raises:
        KeyError: If template doesn't exist
    """
    if template_name not in TEMPLATE_FILES:
        available = list(TEMPLATE_FILES.keys())
        raise KeyError(f"Template '{template_name}' not found. Available: {available}")

    return TEMPLATES_DIR / TEMPLATE_FILES[template_name]

def list_templates() -> dict[str, dict[str, str]]:
    """List all available templates with metadata.

    Returns:
        Dictionary mapping template names to metadata
    """
    templates = {}
    for name, filename in TEMPLATE_FILES.items():
        template_path = TEMPLATES_DIR / filename
        if template_path.exists():
            templates[name] = {
                "filename": filename,
                "path": str(template_path),
                "title": name.replace("-", " ").title(),
            }
    return templates
