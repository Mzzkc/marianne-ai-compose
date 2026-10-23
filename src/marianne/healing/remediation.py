"""Job-level adapter for the conductor's shared healing coordinator."""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from marianne.core.checkpoint import CheckpointState, SheetStatus
from marianne.core.config import JobConfig
from marianne.healing.context import ErrorContext
from marianne.healing.coordinator import HealingReport, SelfHealingCoordinator
from marianne.healing.registry import create_default_registry
from marianne.spec.loader import SpecCorpusError, SpecCorpusLoader


@dataclass
class RemediationResult:
    """Reports for the selected sheets and context-loading warnings."""

    reports: list[HealingReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.reports


class RemediationService:
    """Reuse the healing engine against persisted state, without saving state."""

    async def run(
        self,
        job: CheckpointState,
        workspace: str,
        *,
        sheet_filter: int | None = None,
        auto_confirm: bool = False,
        dry_run: bool = False,
    ) -> RemediationResult:
        result = RemediationResult()
        config = None
        if job.config_snapshot:
            try:
                config = JobConfig.model_validate(job.config_snapshot)
            except ValidationError as exc:
                result.warnings.append(f"Cannot reload score configuration: {exc}")
        spec_metadata: dict[str, object] = {}
        if config:
            fragments = list(config.spec.fragments)
            if config.spec.spec_dir:
                try:
                    fragments = SpecCorpusLoader.load(Path(workspace) / config.spec.spec_dir)
                except (SpecCorpusError, OSError) as exc:
                    spec_metadata["spec_load_error"] = str(exc)
                    result.warnings.append(f"Cannot load spec corpus: {exc}")
            spec_metadata["spec_fragments"] = [f.model_dump() for f in fragments]
        registry = create_default_registry()
        for number, sheet in sorted(job.sheets.items()):
            if sheet_filter is not None and number != sheet_filter:
                continue
            if sheet.status != SheetStatus.FAILED and not (
                sheet_filter is not None
                and not sheet.status.is_terminal
                and (sheet.error_code or sheet.error_message)
            ):
                continue
            context = ErrorContext.from_checkpoint_sheet(
                sheet,
                number,
                workspace=workspace,
                config_path=job.config_path,
                config=config,
            )
            context.metadata.update(spec_metadata)
            context.metadata["config_snapshot"] = job.config_snapshot
            context.metadata["failed_sheet_count"] = sum(
                s.status == SheetStatus.FAILED for s in job.sheets.values()
            )
            # Each sheet is a separate healing cycle; sharing one coordinator
            # would exhaust its max_healing_attempts after the second sheet.
            coordinator = SelfHealingCoordinator(
                registry,
                auto_confirm=auto_confirm or dry_run,
                dry_run=dry_run,
            )
            result.reports.append(await coordinator.heal(context))
        return result
