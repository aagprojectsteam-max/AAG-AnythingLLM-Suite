"""Evidence-bound diagnostic investigation and hypothesis falsification."""

from .engine import InvestigationEngine, InvestigationError
from .service import build_engine

__all__ = ["InvestigationEngine", "InvestigationError", "build_engine"]
