"""Build and measure a local composition corpus from explicitly selected sources."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import typer

from marianne.corpus import TrainingExample, extract_in_repo
from marianne.corpus.ingest import dedupe, ingest_spool
from marianne.corpus.metrics import corpus_stats
from marianne.telemetry.consent import ConsentRecord

corpus_app = typer.Typer(
    name="corpus",
    help="Build and measure a local composition corpus.",
    no_args_is_help=True,
)


def _source_paths(sources: str) -> list[Path]:
    paths = [Path(part.strip()).expanduser() for part in sources.split(",") if part.strip()]
    if not paths:
        raise typer.BadParameter("Choose score directories or telemetry JSONL files.")
    return paths


def _load(paths: list[Path]) -> list[TrainingExample]:
    records = []
    try:
        for path in paths:
            if path.is_dir():
                records.extend(extract_in_repo([path]))
            elif path.is_file() and path.suffix.lower() == ".jsonl":
                records.extend(ingest_spool(path))
            else:
                raise typer.BadParameter(
                    "Sources must be score directories or telemetry JSONL files."
                )
    except (OSError, ValueError):
        typer.echo("Corpus source could not be read. Check source format and storage.", err=True)
        raise typer.Exit(1) from None
    return dedupe(records)


@corpus_app.command()
def build(
    sources: str = typer.Option(
        "examples,scores", help="Comma-separated score dirs or JSONL spools."
    ),
    output: Path = typer.Option(..., help="Destination JSONL file (private local export)."),
) -> None:
    """Write deduplicated examples locally. Review before sharing or training."""
    paths = _source_paths(sources)
    destination = output.expanduser().resolve()
    protected = {path.resolve() for path in paths} | {ConsentRecord().path.resolve()}
    # destination is the resolved output path; resolve() preserves the basename,
    # so checking it covers the unresolved output name as well.
    if destination in protected or destination.name.endswith(".lock"):
        raise typer.BadParameter("Output must differ from sources, consent state, and lock files.")
    if destination.suffix.lower() in {".yaml", ".yml"} and any(
        path.is_dir() and destination.is_relative_to(path.resolve()) for path in paths
    ):
        raise typer.BadParameter("Output must not replace a source score.")
    records = _load(paths)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=destination.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(record.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    except OSError:
        typer.echo("Corpus output could not be written. Check destination storage.", err=True)
        raise typer.Exit(1) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    typer.echo(f"Wrote {len(records)} examples. Review local data before sharing or training.")


@corpus_app.command()
def stats(
    sources: str = typer.Option(
        "examples,scores", help="Comma-separated score dirs or JSONL spools."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable counts."),
) -> None:
    """Count distinct outputs and annotations against the 200-example volume target."""
    result = corpus_stats(_load(_source_paths(sources)))
    if json_output:
        typer.echo(result.model_dump_json())
    else:
        typer.echo(f"Total examples: {result.total_examples} / {result.target_volume}")
        typer.echo(f"Annotated: {result.annotated}")
        typer.echo(f"By source: {result.by_source_kind}")
        typer.echo(f"By category: {result.by_category}")
        typer.echo("Volume alone does not establish diversity, permission, or training readiness.")
