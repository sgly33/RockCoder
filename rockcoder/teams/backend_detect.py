 
from __future__ import annotations

from rockcoder.teams.models import BackendType


def detect_backend(is_interactive: bool = True) -> BackendType:
    return BackendType.IN_PROCESS
