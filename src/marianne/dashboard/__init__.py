"""Marianne Dashboard - Web interface for job monitoring.

This module provides a FastAPI-based REST API for:
- Listing jobs and their status
- Viewing detailed job information
- Monitoring job progress

Usage:
    from marianne.dashboard import create_app

    app = create_app(state_dir="/path/to/state")
    # Run with uvicorn: uvicorn.run(app, host="0.0.0.0", port=8000)
"""

from marianne.dashboard.app import create_app, get_state_backend

__all__ = ["create_app", "get_state_backend"]
