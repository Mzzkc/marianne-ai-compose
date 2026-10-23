"""Score-local settings; consent is always a separate user-level decision."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TelemetryConfig(BaseModel):
    """Default-off collection with local inspection before optional submission."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Opt this score into local telemetry staging; requires "
        "separate user consent (mzt telemetry consent --grant).",
    )
    endpoint: str | None = Field(
        default=None,
        description="Reserved: no runtime reader. Submission endpoint is chosen "
        "per flush via `mzt telemetry flush --push --endpoint`.",
    )
    spool_path: Path | None = Field(
        default=None,
        description="Custom JSONL spool for this score (default: "
        "~/.marianne/telemetry.jsonl); cannot be a reserved .lock filename "
        "or overlap consent/control files.",
    )
    submit_mode: Literal["local_only", "push"] = Field(
        default="local_only",
        description="Reserved: no runtime reader. Collection is local "
        "regardless; sending happens only through the explicit flush command.",
    )
