"""Migration support for workspace-local outcomes to global store.

This module implements the migration strategy from Movement III design:
- Import existing .marianne-outcomes.json on first use or explicit command
- Scan common workspace locations
- Run pattern detection on imported data
- Preserve workspace-local files (non-destructive)

Migration Flow:
1. On GlobalLearningStore initialization, check if empty
2. Scan common workspace locations for .marianne-outcomes.json
3. For each found, import outcomes to executions table
4. Run pattern detection on imported data
5. Log migration summary to user
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from marianne.core.checkpoint import SheetStatus, ValidationDetailDict
from marianne.core.logging import get_logger
from marianne.learning.outcomes import SheetOutcome


class _OutcomeDict(TypedDict, total=False):
    """Structure of outcome entries in .marianne-outcomes.json.

    All fields are optional (total=False) because legacy files may have
    incomplete data or use different key names.
    """

    sheet_id: str
    id: str  # Legacy alias for sheet_id
    job_id: str
    final_status: str
    status: str  # Legacy alias for final_status
    validation_results: list[ValidationDetailDict]
    validation_pass_rate: float
    execution_duration: float
    retry_count: int
    completion_mode_used: bool
    first_attempt_success: bool  # Deprecated: renamed to success_without_retry in DB
    timestamp: str

if TYPE_CHECKING:
    from marianne.learning.aggregator import PatternAggregator
    from marianne.learning.global_store import GlobalLearningStore

# Module-level logger for migration operations
_logger = get_logger("learning.migration")


@dataclass
class MigrationResult:
    """Result of a migration operation.

    Attributes:
        workspaces_found: Number of workspaces with outcomes.
        outcomes_imported: Total outcomes imported.
        patterns_detected: Patterns detected from imported outcomes.
        errors: Any errors encountered during migration.
        skipped_workspaces: Workspaces skipped (already imported, etc.).
        imported_workspaces: Workspaces successfully imported.
    """

    errors: list[str] = field(default_factory=list)
    skipped_workspaces: list[str] = field(default_factory=list)
    imported_workspaces: list[str] = field(default_factory=list)
    workspaces_found: int = 0
    outcomes_imported: int = 0
    patterns_detected: int = 0

    def __repr__(self) -> str:
        return (
            f"MigrationResult(workspaces={self.workspaces_found}, "
            f"outcomes={self.outcomes_imported}, patterns={self.patterns_detected})"
        )


# Default locations to scan for workspace-local outcomes
DEFAULT_SCAN_PATTERNS = [
    "~/.marianne/*/.marianne-outcomes.json",
    "./*-workspace/.marianne-outcomes.json",
    "./workspace/.marianne-outcomes.json",
    "./.marianne-outcomes.json",
    "./evolution-workspace*/.marianne-outcomes.json",
    "./global-learning-workspace/.marianne-outcomes.json",
]


class OutcomeMigrator:
    """Migrates workspace-local outcomes to the global store.

    This migrator scans for existing .marianne-outcomes.json files and
    imports their contents into the global SQLite database, enabling
    cross-workspace learning from historical data.

    Migration is:
    - Non-destructive: Original files are preserved
    - Idempotent: Already-imported outcomes are skipped
    - Pattern-aware: Runs pattern detection after import

    Usage:
        migrator = OutcomeMigrator(global_store)
        result = migrator.migrate_all()
        print(f"Imported {result.outcomes_imported} outcomes")
    """

    def __init__(
        self,
        global_store: "GlobalLearningStore",
        aggregator: "PatternAggregator | None" = None,
    ) -> None:
        """Initialize the outcome migrator.

        Args:
            global_store: Global learning store to import into.
            aggregator: Optional pattern aggregator for pattern detection.
        """
        self._store = global_store
        self._aggregator = aggregator

        # Track imported workspaces to avoid duplicates
        self._imported_workspace_hashes: set[str] = set()

    def migrate_all(
        self,
        scan_patterns: list[str] | None = None,
        additional_paths: list[Path] | None = None,
    ) -> MigrationResult:
        """Migrate all discoverable workspace-local outcomes.

        Scans standard locations plus any additional paths for
        .marianne-outcomes.json files and imports them.

        Args:
            scan_patterns: Glob patterns to scan (defaults to standard locations).
            additional_paths: Additional specific paths to scan.

        Returns:
            MigrationResult with import statistics.
        """
        result = MigrationResult()
        patterns = scan_patterns or DEFAULT_SCAN_PATTERNS

        # Collect all outcome files to migrate
        outcome_files: list[Path] = []

        # Expand glob patterns
        for pattern in patterns:
            expanded = Path(pattern).expanduser()
            if "*" in str(expanded):
                # It's a glob pattern - find the first non-glob parent
                # e.g., "./*-workspace/.marianne-outcomes.json" -> glob from "."
                pattern_str = str(expanded)
                parts = pattern_str.split("/")
                base_parts: list[str] = []
                glob_parts: list[str] = []
                in_glob = False
                for part in parts:
                    if "*" in part or in_glob:
                        in_glob = True
                        glob_parts.append(part)
                    else:
                        base_parts.append(part)

                base_path = Path("/".join(base_parts)) if base_parts else Path(".")
                glob_pattern = "/".join(glob_parts)

                if base_path.exists():
                    outcome_files.extend(base_path.glob(glob_pattern))
            elif expanded.exists() and expanded.is_file():
                outcome_files.append(expanded)

        # Add additional paths
        if additional_paths:
            for path in additional_paths:
                if path.exists() and path.is_file():
                    outcome_files.append(path)

        # Deduplicate by resolved path
        unique_files = list({f.resolve() for f in outcome_files})
        result.workspaces_found = len(unique_files)

        _logger.info("migration_files_found", count=len(unique_files))

        # Import each file
        for outcome_file in unique_files:
            try:
                imported = self._migrate_file(outcome_file)
                if imported > 0:
                    result.outcomes_imported += imported
                    result.imported_workspaces.append(str(outcome_file.parent))
                else:
                    result.skipped_workspaces.append(str(outcome_file.parent))
            except Exception as e:
                error_msg = f"Error migrating {outcome_file}: {e}"
                _logger.warning(error_msg)
                result.errors.append(error_msg)

        # Run pattern detection on imported data if aggregator available
        if self._aggregator and result.outcomes_imported > 0:
            try:
                detected_count: int = self._detect_patterns_from_store()
                result.patterns_detected = detected_count
            except Exception as e:
                _logger.warning("pattern_detection_error", error=str(e), exc_info=True)
                result.errors.append(f"Pattern detection error: {e}")

        _logger.info(
            "migration_complete",
            outcomes=result.outcomes_imported,
            patterns=result.patterns_detected,
        )

        return result

    def migrate_workspace(self, workspace_path: Path) -> MigrationResult:
        """Migrate a single workspace's outcomes.

        Args:
            workspace_path: Path to the workspace directory.

        Returns:
            MigrationResult for this workspace.
        """
        result = MigrationResult()

        # Look for outcome file in workspace
        outcome_file = workspace_path / ".marianne-outcomes.json"
        if not outcome_file.exists():
            result.errors.append(f"No outcomes file found in {workspace_path}")
            return result

        result.workspaces_found = 1

        try:
            imported = self._migrate_file(outcome_file)
            result.outcomes_imported = imported
            if imported > 0:
                result.imported_workspaces.append(str(workspace_path))
            else:
                result.skipped_workspaces.append(str(workspace_path))
        except Exception as e:
            _logger.warning(
                "workspace_migration_error",
                workspace=str(workspace_path),
                error=str(e),
                exc_info=True,
            )
            result.errors.append(f"Error: {e}")

        return result

    def _migrate_file(self, outcome_file: Path) -> int:
        """Migrate a single outcome file.

        Args:
            outcome_file: Path to .marianne-outcomes.json file.

        Returns:
            Number of outcomes imported.
        """
        # Get workspace path from outcome file location
        workspace_path = outcome_file.parent

        # Check if already imported
        workspace_hash = self._store.hash_workspace(workspace_path)
        if workspace_hash in self._imported_workspace_hashes:
            _logger.debug("skipping_imported_workspace", workspace=str(workspace_path))
            return 0

        # Load outcomes from JSON file
        with open(outcome_file) as f:
            data = json.load(f)

        outcomes = data.get("outcomes", [])
        if not outcomes:
            return 0

        imported_count = 0
        skipped_count = 0

        for outcome_data in outcomes:
            try:
                # Convert to SheetOutcome object
                outcome = self._parse_outcome(outcome_data)
                if outcome:
                    self._store.record_outcome(
                        outcome=outcome,
                        workspace_path=workspace_path,
                        model=outcome_data.get("model"),
                    )
                    imported_count += 1
            except Exception as e:
                skipped_count += 1
                _logger.warning("skipping_invalid_outcome", error=str(e), exc_info=True)
                continue

        # Mark workspace as imported
        self._imported_workspace_hashes.add(workspace_hash)

        if skipped_count > 0:
            _logger.warning(
                "outcomes_import_incomplete",
                imported=imported_count,
                skipped=skipped_count,
                total=len(outcomes),
                workspace=workspace_path.name,
            )
        _logger.info("outcomes_imported", count=imported_count, workspace=workspace_path.name)

        return imported_count

    def _parse_outcome(self, data: _OutcomeDict) -> SheetOutcome | None:
        """Parse an outcome from JSON data.

        Args:
            data: Dictionary from .marianne-outcomes.json.

        Returns:
            SheetOutcome object, or None if parsing fails.
        """
        try:
            # Handle different formats that might exist in legacy files
            sheet_id = data.get("sheet_id") or data.get("id", "unknown")
            job_id = data.get("job_id") or sheet_id.rsplit("-", 1)[0]

            # Parse status - handle string or enum
            status_value = data.get("final_status") or data.get("status", "unknown")
            if isinstance(status_value, str):
                try:
                    final_status = SheetStatus(status_value.lower())
                except ValueError:
                    final_status = SheetStatus.FAILED
            else:
                final_status = status_value

            # Parse validation results
            validation_results = data.get("validation_results", [])
            if isinstance(validation_results, list):
                pass_count = sum(
                    1 for v in validation_results
                    if v.get("passed", False)
                )
                total_count = len(validation_results) if validation_results else 1
                validation_pass_rate = pass_count / total_count if total_count > 0 else 0.0
            else:
                validation_pass_rate = data.get("validation_pass_rate", 0.0)

            # Parse timestamp
            timestamp = data.get("timestamp")
            if isinstance(timestamp, str):
                try:
                    parsed_timestamp = datetime.fromisoformat(timestamp)
                except ValueError:
                    parsed_timestamp = datetime.now()
            else:
                parsed_timestamp = datetime.now()

            return SheetOutcome(
                sheet_id=sheet_id,
                job_id=job_id,
                validation_results=validation_results,
                execution_duration=data.get("execution_duration", 0.0),
                retry_count=data.get("retry_count", 0),
                completion_mode_used=data.get("completion_mode_used", False),
                final_status=final_status,
                validation_pass_rate=validation_pass_rate,
                success_without_retry=bool(
                    data.get("success_without_retry", data.get("first_attempt_success", False))
                ),
                timestamp=parsed_timestamp,
            )
        except Exception as e:
            _logger.warning("outcome_parse_failed", error=str(e), exc_info=True)
            return None

    def _detect_patterns_from_store(self) -> int:
        """Run pattern detection on data in the store.

        Uses the PatternAggregator to update priority scores for
        all patterns and prune deprecated ones. This ensures that
        after migration, patterns are properly weighted.

        Returns:
            Number of patterns detected.
        """
        if self._aggregator is None:
            return 0

        try:
            # Update priorities for all existing patterns
            self._aggregator._update_all_priorities()

            # Prune patterns that have become ineffective
            pruned_count = self._aggregator.prune_deprecated_patterns()
            _logger.debug("patterns_pruned", count=pruned_count)

            # Return current pattern count
            stats = self._store.get_execution_stats()
            return int(stats.get("total_patterns", 0))
        except Exception as e:
            _logger.warning("pattern_detection_error", error=str(e))
            return 0


def migrate_existing_outcomes(
    global_store: "GlobalLearningStore",
    scan_patterns: list[str] | None = None,
    additional_paths: list[Path] | None = None,
) -> MigrationResult:
    """Convenience function to migrate all existing outcomes.

    Args:
        global_store: Global learning store to import into.
        scan_patterns: Optional custom scan patterns.
        additional_paths: Optional additional paths to scan.

    Returns:
        MigrationResult with import statistics.
    """
    migrator = OutcomeMigrator(global_store)
    return migrator.migrate_all(
        scan_patterns=scan_patterns,
        additional_paths=additional_paths,
    )


def check_migration_status(global_store: "GlobalLearningStore") -> dict[str, Any]:
    """Check the current migration status.

    Args:
        global_store: Global learning store to check.

    Returns:
        Dictionary with migration status information.
    """
    stats = global_store.get_execution_stats()

    return {
        "total_executions": stats.get("total_executions", 0),
        "total_patterns": stats.get("total_patterns", 0),
        "unique_workspaces": stats.get("unique_workspaces", 0),
        "needs_migration": stats.get("total_executions", 0) == 0,
        "avg_pattern_effectiveness": stats.get("avg_pattern_effectiveness", 0.0),
    }
