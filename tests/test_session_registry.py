"""Behavior tests for named-session resume targets."""

from argparse import Namespace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1] / "resume-session" / "scripts" / "session_registry.py"
)


def load_registry_module():
    """Load the registry script as a module for focused behavior tests."""
    spec = spec_from_file_location("session_registry", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codex_resume_token_uses_session_id() -> None:
    """Codex resume targets must not depend on mutable native names."""
    registry = load_registry_module()

    token = registry._resume_token(
        "codex",
        "01a15e10-a0f3-7401-9800-581b0e9cb880",
        "/home/robin/Testing",
    )

    assert token == "01a15e10-a0f3-7401-9800-581b0e9cb880"


def test_codex_resume_command_uses_session_id(monkeypatch, capsys) -> None:
    """The printed fallback command must also use the stable session ID."""
    registry = load_registry_module()
    session_id = "01a15e10-a0f3-7401-9800-581b0e9cb880"
    monkeypatch.setattr(registry, "CURRENT_AGENT", "codex")
    monkeypatch.setattr(
        registry,
        "load_index",
        lambda: {
            "DPAPI-BN": {
                "cwd": "/home/robin/Testing",
                "session_id": session_id,
            }
        },
    )
    monkeypatch.setattr(
        registry,
        "_resolve_resume_cwd",
        lambda _session_id, _cwd: (
            "/home/robin/Testing",
            Path("/tmp/existing-rollout.jsonl"),
        ),
    )

    result = registry.cmd_resume_cmd(Namespace(name="DPAPI-BN"))

    assert result == 0
    assert capsys.readouterr().out == (
        f"cd /home/robin/Testing && codex resume {session_id}\n"
    )
