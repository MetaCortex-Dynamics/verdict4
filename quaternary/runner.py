"""
Quaternary loop runner.

The runner is a protocol over user-supplied callables.
It contains zero domain logic.

User supplies:
    generate(task, exclusions) -> output
    check(output) -> CheckResult
    gather(needed) -> evidence       (for MAYBE)
    recheck(output, evidence) -> CheckResult
    is_resolved(dep) -> bool         (for IFF)
    is_failed(dep) -> bool           (for IFF)

The runner handles the four branches.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .core import CheckResult, Verdict


@runtime_checkable
class GenerateFn(Protocol):
    def __call__(self, task: Any, *, exclusions: list[str]) -> Any: ...


@runtime_checkable
class CheckFn(Protocol):
    def __call__(self, output: Any) -> CheckResult: ...


@runtime_checkable
class GatherFn(Protocol):
    def __call__(self, needed: str) -> Any: ...


@runtime_checkable
class RecheckFn(Protocol):
    def __call__(self, output: Any, evidence: Any | None = None) -> CheckResult: ...


@runtime_checkable
class DepCheckFn(Protocol):
    def __call__(self, dependency: str) -> bool: ...


@dataclass
class LoopOutcome:
    """What the loop produced on termination."""

    status: str  # "accepted" | "held" | "blocked" | "exhausted"
    output: Any | None = None
    exclusions: list[str] = field(default_factory=list)
    held_reason: str | None = None
    blocked_by: str | None = None
    rounds: int = 0
    contradiction: list[tuple[str, str]] | None = None  # T8: flagged contradictions


def _detect_contradictions(exclusions: list[str]) -> list[tuple[str, str]]:
    """T8 (Cell C): Flag exclusion pairs that may contradict.

    Heuristic: if one exclusion is a substring negation of another.
    Not a resolver — a flag. The developer inspects.
    """
    contradictions = []
    negation_prefixes = ("not ", "no ", "don't ", "do not ", "never ", "avoid ")
    for i, a in enumerate(exclusions):
        for b in exclusions[i + 1 :]:
            a_lower, b_lower = a.lower(), b.lower()
            for prefix in negation_prefixes:
                if (
                    a_lower.startswith(prefix)
                    and a_lower[len(prefix) :].strip() == b_lower
                    or b_lower.startswith(prefix)
                    and b_lower[len(prefix) :].strip() == a_lower
                ):
                    contradictions.append((a, b))
    return contradictions


def _needed_text(needed: object) -> str | None:
    """Return legacy string needs without interpreting typed controller needs."""

    return needed if isinstance(needed, str) else None


def run(
    task: Any,
    *,
    generate: GenerateFn,
    check: CheckFn,
    gather: GatherFn | None = None,
    recheck: RecheckFn | None = None,
    is_resolved: DepCheckFn | None = None,
    is_failed: DepCheckFn | None = None,
    max_rounds: int = 10,
    on_hold: Callable[[Any, str], None] | None = None,
    on_blocked: Callable[[Any, str], None] | None = None,
) -> LoopOutcome:
    """Run a quaternary evaluation loop.

    Returns LoopOutcome with status, output, accumulated exclusions,
    and any flagged contradictions.
    """
    exclusions: list[str] = []

    for round_num in range(1, max_rounds + 1):
        output = generate(task, exclusions=exclusions)
        result = check(output)

        # --- YES ---
        if result.verdict == Verdict.YES:
            return LoopOutcome(
                status="accepted",
                output=output,
                exclusions=exclusions,
                rounds=round_num,
                contradiction=_detect_contradictions(exclusions) or None,
            )

        # --- NO ---
        elif result.verdict == Verdict.NO:
            assert result.reason is not None  # enforced by CheckResult
            exclusions.append(result.reason)
            continue

        # --- MAYBE --- (T6: bounded recheck)
        elif result.verdict == Verdict.MAYBE:
            needed = _needed_text(result.needed)
            if needed is None:
                reason = "typed MAYBE need requires QICTController"
                if on_hold:
                    on_hold(output, reason)
                return LoopOutcome(
                    status="held",
                    output=output,
                    exclusions=exclusions,
                    held_reason=reason,
                    rounds=round_num,
                    contradiction=_detect_contradictions(exclusions) or None,
                )
            if gather is None or recheck is None:
                # No gather/recheck supplied — hold immediately
                if on_hold:
                    on_hold(output, needed)
                return LoopOutcome(
                    status="held",
                    output=output,
                    exclusions=exclusions,
                    held_reason=needed,
                    rounds=round_num,
                    contradiction=_detect_contradictions(exclusions) or None,
                )

            evidence = gather(needed)
            result2 = recheck(output, evidence)

            if result2.verdict == Verdict.YES:
                return LoopOutcome(
                    status="accepted",
                    output=output,
                    exclusions=exclusions,
                    rounds=round_num,
                    contradiction=_detect_contradictions(exclusions) or None,
                )
            elif result2.verdict == Verdict.NO:
                assert result2.reason is not None
                exclusions.append(result2.reason)
                continue
            else:
                # Second MAYBE or IFF after gather → hold, do not loop (T6)
                if on_hold:
                    on_hold(
                        output,
                        _needed_text(result2.needed)
                        or result2.dependency
                        or "unresolvable",
                    )
                return LoopOutcome(
                    status="held",
                    output=output,
                    exclusions=exclusions,
                    held_reason=_needed_text(result2.needed) or result2.dependency,
                    rounds=round_num,
                    contradiction=_detect_contradictions(exclusions) or None,
                )

        # --- IFF ---
        elif result.verdict == Verdict.IFF:
            dep = result.dependency
            assert dep is not None  # enforced by CheckResult

            if is_resolved is not None and is_resolved(dep) and recheck is not None:
                # Dependency resolved — recheck output
                result3 = recheck(output)
                if result3.verdict == Verdict.YES:
                    return LoopOutcome(
                        status="accepted",
                        output=output,
                        exclusions=exclusions,
                        rounds=round_num,
                        contradiction=_detect_contradictions(exclusions) or None,
                    )
                elif result3.verdict == Verdict.NO:
                    assert result3.reason is not None
                    exclusions.append(result3.reason)
                    continue

            if is_failed is not None and is_failed(dep) and recheck is not None:
                # Dependency failed — recheck without it
                result4 = recheck(output)
                if result4.verdict == Verdict.YES:
                    return LoopOutcome(
                        status="accepted",
                        output=output,
                        exclusions=exclusions,
                        rounds=round_num,
                        contradiction=_detect_contradictions(exclusions) or None,
                    )
                elif result4.verdict == Verdict.NO:
                    assert result4.reason is not None
                    exclusions.append(result4.reason)
                    continue

            # Dependency unresolved, no resolver supplied — block
            if on_blocked:
                on_blocked(output, dep)
            return LoopOutcome(
                status="blocked",
                output=output,
                exclusions=exclusions,
                blocked_by=dep,
                rounds=round_num,
                contradiction=_detect_contradictions(exclusions) or None,
            )

    # Exhausted max rounds
    return LoopOutcome(
        status="exhausted",
        output=None,
        exclusions=exclusions,
        rounds=max_rounds,
        contradiction=_detect_contradictions(exclusions) or None,
    )
