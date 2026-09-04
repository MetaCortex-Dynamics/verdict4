"""
Quaternary Verdict — four-value evaluation for agentic loops.

Replace boolean pass/fail with four states:
    NO    — fails a named condition
    YES   — satisfies conditions
    MAYBE — cannot be evaluated with available evidence
    IFF   — correct if and only if a dependency holds

https://metacortexdynamics.substack.com/p/your-loop-has-two-states-it-needs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, TypeAlias


class Verdict(IntEnum):
    """Four-value evaluation result.

    Binary loops compress MAYBE and IFF into failure.
    That compression destroys correct work.
    """

    NO = 0
    YES = 1
    MAYBE = 2
    IFF = 3


@dataclass(frozen=True, slots=True)
class EvidenceNeed:
    """A canonical request for one finite piece of absent evidence."""

    evidence: str

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("EvidenceNeed requires a non-empty canonical identifier.")


@dataclass(frozen=True, slots=True)
class ComputeNeed:
    """A canonical request for one further application of the loop body."""

    step: str

    def __post_init__(self) -> None:
        if not self.step:
            raise ValueError("ComputeNeed requires a non-empty canonical identifier.")


Needed: TypeAlias = EvidenceNeed | ComputeNeed


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Structured evaluation carrying its own operational consequence.

    NO    → reason: why the output failed (named, not anonymous)
    YES   → accepted (reason/needed/dependency unused)
    MAYBE → needed: what evidence is missing
    IFF   → dependency: what must hold for the output to hold
    """

    verdict: Verdict
    reason: str | None = None
    needed: str | Needed | None = None
    dependency: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verdict == Verdict.NO and self.reason is None:
            raise ValueError(
                "NO verdict requires a reason. Failure must not be anonymous."
            )
        if self.verdict == Verdict.MAYBE and self.needed is None:
            raise ValueError("MAYBE verdict requires needed evidence.")
        if self.verdict == Verdict.IFF and self.dependency is None:
            raise ValueError("IFF verdict requires a named dependency.")


# --- Constructors (convenience) ---


def no(reason: str, **meta: Any) -> CheckResult:
    """Reject with named reason."""
    return CheckResult(verdict=Verdict.NO, reason=reason, meta=meta)


def yes(**meta: Any) -> CheckResult:
    """Accept."""
    return CheckResult(verdict=Verdict.YES, meta=meta)


def maybe(needed: str | Needed, **meta: Any) -> CheckResult:
    """Hold because typed evidence or computation is needed.

    Strings remain accepted for compatibility with the v0.1.0 runner. The
    finite-rank controller requires EvidenceNeed or ComputeNeed.
    """
    return CheckResult(verdict=Verdict.MAYBE, needed=needed, meta=meta)


def iff(dependency: str, **meta: Any) -> CheckResult:
    """Conditional — correct if dependency holds."""
    return CheckResult(verdict=Verdict.IFF, dependency=dependency, meta=meta)
