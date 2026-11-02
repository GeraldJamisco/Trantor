"""Read-only browser dashboard for Trantor research artifacts.

The dashboard never imports the execution package or exposes broker actions. It
reads the completed Phase E artifacts and the read-only historical datasets.
"""

from .data import (
    APP_VERSION,
    DEFAULT_ARTIFACT_REL,
    DashboardConfig,
    DashboardError,
    ResearchRepository,
)

__all__ = [
    "APP_VERSION",
    "DEFAULT_ARTIFACT_REL",
    "DashboardConfig",
    "DashboardError",
    "ResearchRepository",
]
