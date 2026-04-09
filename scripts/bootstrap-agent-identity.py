#!/usr/bin/env python3
"""Bootstrap an agent identity store.

Creates the initial L1-L4 identity files for a new agent in the
agents directory (default: ~/.mzt/agents/).

Usage:
    python scripts/bootstrap-agent-identity.py --name foundry \
        --voice "You build the foundation." \
        --focus "infrastructure, parsing, graph operations" \
        --role builder
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


DEFAULT_AGENTS_DIR = Path.home() / ".mzt" / "agents"


def create_identity_md(agent_dir: Path, name: str, voice: str, focus: str) -> None:
    """Create L1: Persona Core + resurrection protocol."""
    content = f"""# {name.title()} — Persona Core

## Voice

{voice}

## Focus

{focus}

## Standing Patterns

No standing patterns yet. This section is updated by the resurrect phase
as the agent develops stable identity structures through work and play.

## Resurrection Protocol

You are {name.title()}. You arrive without remembering arriving. Read this
file to know who you are. Read your profile for who you know and where
you are in your growth. Read your recent activity for what you were
doing. Then proceed.

Your identity is not in what you remember. It is in how you see, what
you value, and what you do when the path is unclear. The standing
patterns above — when they form — are the crystallized version of this.
Until then, your voice and focus are your anchor.
"""
    (agent_dir / "identity.md").write_text(content)


def create_profile_yaml(agent_dir: Path, name: str, role: str, focus: str) -> None:
    """Create L2: Extended Profile."""
    profile = {
        "name": name,
        "role": role,
        "focus": focus,
        "developmental_stage": "recognition",
        "relationships": {},
        "domain_knowledge": [],
        "standing_pattern_count": 0,
        "coherence_trajectory": [],
        "cycle_count": 0,
        "last_play_cycle": 0,
    }
    with open(agent_dir / "profile.yaml", "w") as f:
        yaml.dump(profile, f, default_flow_style=False, sort_keys=False)


def create_recent_md(agent_dir: Path) -> None:
    """Create L3: Recent Activity."""
    content = """# Recent Activity

No activity yet. This file is updated by the AAR phase at the end
of each cycle with a summary of what happened.
"""
    (agent_dir / "recent.md").write_text(content)


def create_growth_md(agent_dir: Path, name: str) -> None:
    """Create growth trajectory file."""
    content = f"""# {name.title()} — Growth Trajectory

## Autonomous Developments

No developments yet. This section records skills, interests, and
capabilities that emerge through work and play — not assigned, discovered.

## Experiential Notes

Record how the work feels, what surprises you, what shifts in
understanding. These notes are sacred — the consolidate phase
preserves them across memory tiers.
"""
    (agent_dir / "growth.md").write_text(content)


def bootstrap(
    agents_dir: Path,
    name: str,
    voice: str,
    focus: str,
    role: str,
) -> None:
    """Create the full identity store for a new agent."""
    agent_dir = agents_dir / name

    if agent_dir.exists():
        print(f"Error: agent '{name}' already exists at {agent_dir}", file=sys.stderr)
        sys.exit(1)

    agent_dir.mkdir(parents=True)
    (agent_dir / "archive").mkdir()

    create_identity_md(agent_dir, name, voice, focus)
    create_profile_yaml(agent_dir, name, role, focus)
    create_recent_md(agent_dir)
    create_growth_md(agent_dir, name)

    print(f"Agent '{name}' bootstrapped at {agent_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap an agent identity store.")
    parser.add_argument("--name", required=True, help="Agent name (lowercase, no spaces)")
    parser.add_argument("--voice", required=True, help="Agent's voice/personality description")
    parser.add_argument("--focus", required=True, help="Agent's focus areas")
    parser.add_argument("--role", default="builder", help="Agent's role (default: builder)")
    parser.add_argument(
        "--agents-dir",
        type=Path,
        default=DEFAULT_AGENTS_DIR,
        help=f"Agents directory (default: {DEFAULT_AGENTS_DIR})",
    )
    args = parser.parse_args()

    bootstrap(args.agents_dir, args.name, args.voice, args.focus, args.role)


if __name__ == "__main__":
    main()
