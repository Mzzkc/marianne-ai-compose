"""GPU probing for the Mozart daemon profiler.

Provides a ``GpuProbe`` class that follows the same "try primary → fallback
→ graceful None" pattern as ``SystemProbe``.  Priority chain:

1. **pynvml** — NVIDIA Management Library Python bindings (fast, no subprocess)
2. **nvidia-smi** — shell subprocess fallback
3. **No GPU data** — silent skip, returns empty list

All methods are static.  ``_pynvml_available`` is set once at import time.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

from mozart.core.logging import get_logger

_logger = get_logger("daemon.profiler.gpu_probe")

# Check pynvml availability once at import time.
# Use nvidia-ml-py (not the deprecated pynvml package) — same module name.
# Suppress FutureWarning from the legacy pynvml package at import time
# so it doesn't pollute CLI output (even `mozart status` triggers this).
import warnings

_pynvml_available: bool = False
try:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        import pynvml as _pynvml

    _pynvml_available = True
except ImportError:
    _pynvml = None


@dataclass(frozen=True)
class GpuMetric:
    """Snapshot of a single GPU's current state."""

    index: int
    utilization_pct: float
    memory_used_mb: float
    memory_total_mb: float
    temperature_c: float


class GpuProbe:
    """GPU resource probes following the SystemProbe pattern.

    Each method tries pynvml first, then falls back to nvidia-smi.
    Returns empty list when no GPU is available — callers treat that
    as "no GPU present" (not an error).
    """

    @staticmethod
    def get_gpu_metrics() -> list[GpuMetric]:
        """Get current metrics for all GPUs.

        Priority:
            1. pynvml (fast, in-process)
            2. nvidia-smi subprocess fallback
            3. Empty list (no GPU / no drivers)

        Returns:
            List of GpuMetric, one per GPU.  Empty if no GPU available.
        """
        if _pynvml_available:
            try:
                return GpuProbe._probe_pynvml()
            except Exception:
                _logger.debug("pynvml_probe_failed", exc_info=True)
        # Fallback to nvidia-smi
        try:
            return GpuProbe._probe_nvidia_smi_sync()
        except Exception:
            _logger.debug("nvidia_smi_probe_failed", exc_info=True)
        return []

    @staticmethod
    async def get_gpu_metrics_async() -> list[GpuMetric]:
        """Async variant of get_gpu_metrics.

        Uses ``asyncio.create_subprocess_exec`` for the nvidia-smi fallback
        so it doesn't block the event loop.

        Returns:
            List of GpuMetric, one per GPU.  Empty if no GPU available.
        """
        if _pynvml_available:
            try:
                return GpuProbe._probe_pynvml()
            except Exception:
                _logger.debug("pynvml_probe_failed", exc_info=True)
        try:
            return await GpuProbe._probe_nvidia_smi_async()
        except Exception:
            _logger.debug("nvidia_smi_async_probe_failed", exc_info=True)
        return []

    # ─── pynvml probe ──────────────────────────────────────────────

    @staticmethod
    def _probe_pynvml() -> list[GpuMetric]:
        """Collect GPU metrics via pynvml.

        Initialises nvml, iterates all devices, reads utilisation /
        memory / temperature, then shuts down.  Any per-device error
        is logged and that GPU is skipped.
        """
        assert _pynvml is not None
        metrics: list[GpuMetric] = []
        _pynvml.nvmlInit()
        try:
            device_count: int = _pynvml.nvmlDeviceGetCount()
            for i in range(device_count):
                try:
                    handle = _pynvml.nvmlDeviceGetHandleByIndex(i)

                    util = _pynvml.nvmlDeviceGetUtilizationRates(handle)
                    mem_info = _pynvml.nvmlDeviceGetMemoryInfo(handle)
                    temp = _pynvml.nvmlDeviceGetTemperature(
                        handle, _pynvml.NVML_TEMPERATURE_GPU
                    )

                    metrics.append(
                        GpuMetric(
                            index=i,
                            utilization_pct=float(util.gpu),
                            memory_used_mb=int(mem_info.used) / (1024 * 1024),
                            memory_total_mb=int(mem_info.total) / (1024 * 1024),
                            temperature_c=float(temp),
                        )
                    )
                except Exception:
                    _logger.debug("pynvml_device_probe_failed", gpu_index=i, exc_info=True)
        finally:
            try:
                _pynvml.nvmlShutdown()
            except Exception:
                pass
        return metrics

    # ─── nvidia-smi fallback (sync) ────────────────────────────────

    @staticmethod
    def _probe_nvidia_smi_sync() -> list[GpuMetric]:
        """Collect GPU metrics by spawning ``nvidia-smi`` synchronously.

        Uses argument-list form (no shell) for safety.
        """
        import subprocess

        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return GpuProbe._parse_nvidia_smi_output(result.stdout)

    # ─── nvidia-smi fallback (async) ───────────────────────────────

    @staticmethod
    async def _probe_nvidia_smi_async() -> list[GpuMetric]:
        """Collect GPU metrics by spawning ``nvidia-smi`` asynchronously.

        Uses ``asyncio.create_subprocess_exec`` (no shell) for safety.
        """
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return []
        return GpuProbe._parse_nvidia_smi_output(stdout.decode())

    # ─── shared parser ─────────────────────────────────────────────

    @staticmethod
    def _parse_nvidia_smi_output(output: str) -> list[GpuMetric]:
        """Parse nvidia-smi CSV output into GpuMetric list.

        Expected format (one line per GPU, comma-separated):
            ``utilization.gpu, memory.used, memory.total, temperature.gpu``
        Values are numeric (nounits flag strips units).
        """
        metrics: list[GpuMetric] = []
        for idx, line in enumerate(output.strip().splitlines()):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            try:
                metrics.append(
                    GpuMetric(
                        index=idx,
                        utilization_pct=float(parts[0]),
                        memory_used_mb=float(parts[1]),
                        memory_total_mb=float(parts[2]),
                        temperature_c=float(parts[3]),
                    )
                )
            except (ValueError, IndexError):
                _logger.debug("nvidia_smi_parse_failed", line=line, exc_info=True)
        return metrics

    @staticmethod
    def is_available() -> bool:
        """Check whether any GPU probing method is available.

        Returns True if pynvml is importable OR nvidia-smi is on PATH.
        """
        if _pynvml_available:
            return True
        return shutil.which("nvidia-smi") is not None


__all__ = ["GpuMetric", "GpuProbe"]
