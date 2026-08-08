"""Embedded local-network survey server for School CSM Control Center."""

from __future__ import annotations

from typing import Any

__all__ = ["SurveyServerController"]


def __getattr__(name: str) -> Any:
    if name == "SurveyServerController":
        from .controller import SurveyServerController

        return SurveyServerController
    raise AttributeError(name)
