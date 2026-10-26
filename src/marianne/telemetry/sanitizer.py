"""Pure recursive redaction for telemetry payloads, including free-text fields.

Pattern redaction cannot identify arbitrary personal facts in prose. Exported
payloads still require user inspection before sharing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from marianne.core.config import JobConfig

# A key is sensitive when ANY of its `_`/`-` separated segments (after
# splitting camelCase) is one of these words, so `access_key`, `auth`,
# `access_token_v2` and `AccessKey` all redact, not only keys that END in a
# sensitive word (the original suffix match let those through).
_SENSITIVE_SEGMENTS = frozenset({
    "apikey", "api", "key", "keys", "token", "password", "passwd",
    "passphrase", "secret", "secrets", "credential", "credentials", "auth",
    "authorization", "access", "cookie", "private", "username",
    "user", "userid", "email", "env", "environment",
})
# Usage counters are what telemetry exists to report; they carry the word
# "token" but hold integers, not credentials. Exact names only.
_COUNTER_KEYS = frozenset({
    "token_count", "tokens_used", "input_tokens", "output_tokens",
    "total_input_tokens", "total_output_tokens", "estimated_tokens",
})
_SEGMENT_SPLIT = re.compile(r"[_\-\s.]+")
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_REDACTIONS = (
    (re.compile(
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?"
        r"-----END (?:[A-Z]+ )?PRIVATE KEY-----", re.DOTALL,
    ), "[SECRET]"),
    # Drop full URLs (including userinfo, hostnames, path and query credentials).
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE), "[URL]"),
    (re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>]+"), "[PATH]"),
    (re.compile(r"(?<![\w])(?:~[^/\s]*[/\\]|/)[^\s\"'<>]*"), "[PATH]"),
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), "[EMAIL]"),
    (re.compile(r"\bBearer\s+[^\s\"'<>]+", re.IGNORECASE), "[SECRET]"),
    # `name = value` / `"name": value` / `'name': 'value'` where the name
    # carries a sensitive word anywhere. An optional closing quote may sit
    # between the name and the separator (JSON and Python dict literals).
    (re.compile(
        r"\b(?:[\w-]*(?:token|password|passwd|passphrase|secret|api[_-]?key|"
        r"credential|auth|access[_-]?key|private[_-]?key)[\w-]*)[\"']?"
        r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"',;}]+)", re.IGNORECASE,
    ), "[SECRET]"),
    (re.compile(r"\b(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]+"), "[SECRET]"),
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), "[SECRET]"),
    (re.compile(r"\b[A-Za-z0-9_+/=-]{32,}\b"), "[SECRET]"),
)


def _text(value: str) -> str:
    for pattern, replacement in _REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _text(str(key)): "[SECRET]" if _sensitive_key(str(key)) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, (str, Path)):
        return _text(str(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Never stringify arbitrary runtime objects into the submission envelope.
    return "[REDACTED]"


def _sensitive_key(key: str) -> bool:
    normalized = _CAMEL_SPLIT.sub("_", key).lower()
    if normalized in _COUNTER_KEYS:
        return False
    segments = [seg for seg in _SEGMENT_SPLIT.split(normalized) if seg]
    return any(seg in _SENSITIVE_SEGMENTS for seg in segments)


def sanitize_score(score: JobConfig | dict[str, Any]) -> dict[str, Any]:
    """Copy score data, removing private strings and telemetry transport settings."""
    raw = score if isinstance(score, dict) else score.model_dump(mode="json")
    payload = {key: value for key, value in raw.items() if key != "telemetry"}
    result: dict[str, Any] = _sanitize(payload)
    return result


def sanitize_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    """Copy outcome metrics and recursively redact nested diagnostic text."""
    result: dict[str, Any] = _sanitize(outcome)
    return result
