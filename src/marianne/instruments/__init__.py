"""Instrument plugin system.

Provides the infrastructure for config-driven instruments — CLI tools
and HTTP APIs that Marianne can use as backends without writing Python code.

An instrument profile is a YAML file that describes everything Marianne
needs: CLI flags, output parsing, error detection, and model metadata.
Adding a new instrument is writing ~30 lines of YAML, not ~300 lines
of Python.

The music metaphor: an instrument is what the musician plays. This package
is the instrument workshop — where instruments are built, tested, and
maintained before being handed to musicians.

Modules:
    loader: Scan directories for instrument YAML profiles, parse and validate.
    (future) cli_backend: Generic CLI execution from an InstrumentProfile.
"""
