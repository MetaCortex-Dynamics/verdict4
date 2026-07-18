"""Quaternary Verdict — four-value evaluation for agentic loops."""

from .core import CheckResult, Verdict, iff, maybe, no, yes
from .runner import LoopOutcome, run

__all__ = [
    "Verdict",
    "CheckResult",
    "LoopOutcome",
    "no",
    "yes",
    "maybe",
    "iff",
    "run",
]

__version__ = "0.1.0"
