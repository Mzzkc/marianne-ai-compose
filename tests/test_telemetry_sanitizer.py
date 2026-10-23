"""Adversarial nested examples exercise the actual staged payload scrubbers."""

import json
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("private", [
    "/home/alice/private/score.yaml", "/Users/alice/secret.txt",
    r"C:\Users\alice\private.txt", r"\\server\alice\file.txt",
    "~/private.txt", "~alice/private.txt", "alice@example.com",
    "sk-abcdefghijklmnopqrstuvwxyz123456", "ghp_abcdefghijklmnopqrstuvwxyz123456",
    "Bearer abcdef123456", "TOKEN=supersecret", "api_key: supersecret",
    "https://alice:supersecret@example.com/private",
])
def test_embedded_private_text_removed(private: str) -> None:
    from marianne.telemetry import sanitize_outcome

    source = {"fix_suggestions": [{"reason": f"failed reading {private} again"}]}
    result = sanitize_outcome(source)
    assert private not in json.dumps(result)
    assert result["fix_suggestions"][0]["reason"].startswith("failed reading ")
    assert source["fix_suggestions"][0]["reason"] == f"failed reading {private} again"


def test_sensitive_fields_keys_and_outcome_metrics() -> None:
    from marianne.telemetry import sanitize_outcome

    result = sanitize_outcome({
        "status": "failed", "retry_count": 2, "token_count": 120,
        "api_key": "short-private-value", "environment": {"PASSWORD": "another-secret"},
        "username": "alice", "/home/alice/work": {"validation_pass_rate": 0.5},
    })
    encoded = json.dumps(result)
    for secret in ("alice", "short-private-value", "another-secret", "/home/"):
        assert secret not in encoded
    assert result["status"] == "failed"
    assert result["retry_count"] == 2
    assert result["token_count"] == 120
    assert result["[PATH]"]["validation_pass_rate"] == 0.5


def test_score_model_scrubbed_without_mutation() -> None:
    from marianne.core.config import JobConfig
    from marianne.telemetry import sanitize_score

    raw = yaml.safe_load(Path("tests/fixtures/test-daemon-job.yaml").read_text())
    raw["workspace"] = "/home/alice/private"
    model = JobConfig.model_validate(raw)
    sanitized = sanitize_score(model)
    assert sanitized["workspace"] == "[PATH]"
    assert sanitized["name"] == "test-daemon-job"
    assert model.workspace == Path("/home/alice/private")
    assert "telemetry" not in sanitized


def test_private_keys_and_quoted_credentials_removed() -> None:
    from marianne.telemetry import sanitize_outcome

    private_key = "-----BEGIN PRIVATE KEY-----\nshort-private-key\n-----END PRIVATE KEY-----"
    result = sanitize_outcome({
        "stderr_tail": f"{private_key}\npassword='multi word secret'",
        "accessToken": "short-token", "apiKey": "short-key",
    })
    encoded = json.dumps(result)
    for secret in ("short-private-key", "multi word secret", "short-token", "short-key"):
        assert secret not in encoded
