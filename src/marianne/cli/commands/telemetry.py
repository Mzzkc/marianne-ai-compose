"""Inspect and explicitly control optional community telemetry."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer

from marianne.telemetry import ConsentRecord, TelemetryConfig
from marianne.telemetry.spool import TelemetrySpool
from marianne.telemetry.transmitter import TelemetryTransmitter

telemetry_app = typer.Typer(
    name="telemetry", help="Inspect and control opt-in community telemetry.", no_args_is_help=True
)


@contextmanager
def _report_failure() -> Iterator[None]:
    try:
        yield
    except (OSError, ValueError, TypeError):
        typer.echo(
            "Telemetry operation failed; local data may be retained. Check storage.", err=True
        )
        raise typer.Exit(1) from None


@telemetry_app.command()
def status(
    spool_path: Path | None = typer.Option(None, help="Inspect a custom score spool."),
) -> None:
    """Show current user consent and staged envelope count. No network access."""
    with _report_failure():
        typer.echo(f"Consent: {ConsentRecord().state.value}")
        typer.echo(f"Staged envelopes: {TelemetrySpool(spool_path).count()}")
        typer.echo("Submission: local_only by default; flush --push requires an explicit endpoint.")


@telemetry_app.command()
def consent(
    grant: bool = typer.Option(False, "--grant", help="Allow enabled scores to stage telemetry."),
    revoke: bool = typer.Option(False, "--revoke", help="Stop collection and future submission."),
    purge: bool = typer.Option(False, "--purge", help="Delete the selected local spool."),
    spool_path: Path | None = typer.Option(None, help="Custom spool to purge."),
) -> None:
    """Explicitly grant, revoke, or purge. Consent alone never enables sending."""
    if (grant and (revoke or purge)) or not (grant or revoke or purge):
        raise typer.BadParameter("Choose --grant, --revoke [--purge], or --purge.")
    with _report_failure():
        record = ConsentRecord()
        if grant:
            record.grant()
            typer.echo("Consent granted. Enabled scores may stage sanitized scores and outcomes.")
            typer.echo("Review exports before sharing; prose may contain personal information.")
        elif revoke:
            record.revoke(spool_path=TelemetrySpool(spool_path).path, purge=purge)
            typer.echo("Consent revoked." + (" Local spool purged." if purge else ""))
        else:
            TelemetrySpool(spool_path).purge()
            typer.echo("Selected spool purged.")


@telemetry_app.command()
def export(
    out: Path | None = typer.Option(None, "--out", help="Private JSONL file; default is stdout."),
    spool_path: Path | None = typer.Option(None, help="Custom score spool."),
) -> None:
    """Export staged envelopes for inspection without sending or deleting them."""
    with _report_failure():
        spool = TelemetrySpool(spool_path)
        protected = {
            path.resolve()
            for path in (
                spool.path,
                spool.path.with_name(spool.path.name + ".lock"),
                spool.path.with_name(spool.path.name + ".submit.lock"),
                ConsentRecord().path,
                ConsentRecord().path.with_name(ConsentRecord().path.name + ".lock"),
            )
        }
        if out is not None and (
            out.resolve() in protected
            or out.name.endswith(".lock")
            or out.resolve().name.endswith(".lock")
        ):
            raise typer.BadParameter("Export destination must differ from telemetry state files.")
        contents = "".join(json.dumps(record, ensure_ascii=True) + "\n" for record in spool.read())
        if out is None:
            typer.echo(contents, nl=False)
            return
        fd, name = tempfile.mkstemp(prefix=f".{out.name}.", dir=out.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(out)
        finally:
            temporary.unlink(missing_ok=True)
        typer.echo("Telemetry export written.")


@telemetry_app.command()
def flush(
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not send or prune any data."),
    push: bool = typer.Option(False, "--push", help="Explicitly allow network submission now."),
    endpoint: str | None = typer.Option(None, help="HTTPS receiver (HTTP allowed on loopback)."),
    spool_path: Path | None = typer.Option(None, help="Custom score spool."),
) -> None:
    """Submit staged data only with --push, a receiver, and current consent."""
    if push and not endpoint:
        raise typer.BadParameter("--push requires --endpoint.")
    config = TelemetryConfig(
        enabled=push,
        submit_mode="push" if push else "local_only",
        endpoint=endpoint,
        spool_path=spool_path,
    )
    count = asyncio.run(TelemetryTransmitter(config).flush(dry_run=dry_run))
    if count < 0:
        typer.echo(
            "Telemetry submission failed or refused; unacknowledged data retained.", err=True
        )
        raise typer.Exit(1)
    typer.echo(f"Submitted envelopes: {count}")
