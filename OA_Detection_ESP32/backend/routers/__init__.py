"""API routers for patients, sessions, device control, and telemetry."""

from fastapi import Request


def get_manager(request: Request):
    """Retrieve the active ConnectionManager from FastAPI app state."""
    return request.app.state.manager

