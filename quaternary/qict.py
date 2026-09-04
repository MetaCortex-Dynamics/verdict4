# SPDX-License-Identifier: MIT
"""Finite-rank Quaternary Iteration Control.

The controller classifies no candidate and stores no candidate activation. It
accounts for evaluator verdicts, applies one-use resource rules, records
dependency bindings and logical lease events, and emits replayable transition
receipts.

The receipt-visible rank is::

    rho = |F \\ E| + B_G + B_K + B_I

Every evaluation transition that remains running consumes exactly one unit of
that rank. Evaluator soundness is deliberately outside this module: arbitrary
or incorrect evaluator outputs cannot violate the termination bound.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TypeAlias

from .core import CheckResult, ComputeNeed, EvidenceNeed, Needed, Verdict


class ControllerStatus(str, Enum):
    """Disposition of the controller itself."""

    RUNNING = "running"
    ACCEPTED = "accepted"
    HELD = "held"


class CandidateDisposition(str, Enum):
    """Candidate admission is distinct from terminal acceptance."""

    SOURCE_ADMISSIBLE = "source_admissible"
    TERMINALLY_ACCEPTED = "terminally_accepted"


class ControllerAction(str, Enum):
    """Actions selected by the controller from evaluator output or events."""

    EXIT_ACCEPT = "exit_accept"
    EXCLUDE_RETRY = "exclude_retry"
    ADMIT_NEXT = "admit_next"
    GATHER = "gather"
    BIND = "bind"
    REEVALUATE = "reevaluate"
    WAIT = "wait"
    EXPIRE = "expire"
    HOLD = "hold"


class LeaseKind(str, Enum):
    EVIDENCE = "evidence"
    DEPENDENCY = "dependency"


class ResolutionOutcome(str, Enum):
    HOLDS = "holds"
    FAILS = "fails"


class ControllerEventKind(str, Enum):
    """Logical controller events; none are evaluator verdicts."""

    EVIDENCE_RETURNED = "evidence_returned"
    DEPENDENCY_RESOLVED = "dependency_resolved"
    ADVANCE_CLOCK = "advance_clock"
    EXPIRE = "expire"
    INVALIDATE_DEPENDENCY = "invalidate_dependency"


@dataclass(frozen=True, slots=True)
class DependencyRecord:
    """Persistent one-use dependency record D = (U^I, V, R, Omega)."""

    used: frozenset[str] = field(default_factory=frozenset)
    unresolved: frozenset[str] = field(default_factory=frozenset)
    relation: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    outcomes: tuple[tuple[str, ResolutionOutcome], ...] = ()

    def __post_init__(self) -> None:
        if not self.unresolved <= self.used:
            raise ValueError("Unresolved dependencies must have been bound.")
        if any(
            source not in self.unresolved or target not in self.unresolved
            for source, target in self.relation
        ):
            raise ValueError("Dependency arcs must join unresolved vertices.")
        outcome_keys = [dependency for dependency, _ in self.outcomes]
        if len(outcome_keys) != len(set(outcome_keys)):
            raise ValueError("A dependency may have only one recorded outcome.")
        if not set(outcome_keys) <= (set(self.used) - set(self.unresolved)):
            raise ValueError("Outcomes belong only to resolved dependencies.")
        if not _acyclic(self.unresolved, self.relation):
            raise ValueError("The unresolved dependency relation must be acyclic.")

    def outcome_for(self, dependency: str) -> ResolutionOutcome | None:
        """Return the recorded outcome for a resolved dependency."""

        return dict(self.outcomes).get(dependency)


@dataclass(frozen=True, slots=True)
class Lease:
    lease_id: str
    kind: LeaseKind
    obligation: str
    deadline: int


@dataclass(frozen=True, slots=True)
class ControllerState:
    """Receipt-visible accounting state; contains no candidate activation."""

    exclusions: frozenset[str] = field(default_factory=frozenset)
    used_evidence: frozenset[str] = field(default_factory=frozenset)
    used_compute: frozenset[str] = field(default_factory=frozenset)
    dependencies: DependencyRecord = field(default_factory=DependencyRecord)
    evidence_budget: int = 0
    compute_budget: int = 0
    dependency_budget: int = 0
    status: ControllerStatus = ControllerStatus.RUNNING
    candidate_disposition: CandidateDisposition | None = None
    evaluation_count: int = 0
    clock: int = 0
    pending_lease: Lease | None = None
    held_reason: str | None = None

    def __post_init__(self) -> None:
        if min(self.evidence_budget, self.compute_budget, self.dependency_budget) < 0:
            raise ValueError("Controller budgets cannot be negative.")
        if self.evaluation_count < 0 or self.clock < 0:
            raise ValueError("Controller indices cannot be negative.")
        if self.status != ControllerStatus.RUNNING and self.pending_lease is not None:
            raise ValueError("A terminal controller cannot retain a pending lease.")


@dataclass(frozen=True, slots=True)
class QICTConfig:
    """Finite alphabets, allocations, admissibility sets, and lease duration."""

    failure_alphabet: AbstractSet[str] = field(default_factory=frozenset)
    evidence_alphabet: AbstractSet[str] = field(default_factory=frozenset)
    compute_alphabet: AbstractSet[str] = field(default_factory=frozenset)
    dependency_alphabet: AbstractSet[str] = field(default_factory=frozenset)
    evidence_budget: int | None = None
    compute_budget: int | None = None
    dependency_budget: int | None = None
    admissible_failures: AbstractSet[str] | None = None
    admissible_dependencies: AbstractSet[str] | None = None
    lease_ticks: int = 1

    def __post_init__(self) -> None:
        for name in (
            "failure_alphabet",
            "evidence_alphabet",
            "compute_alphabet",
            "dependency_alphabet",
        ):
            object.__setattr__(self, name, frozenset(getattr(self, name)))

        if self.admissible_failures is None:
            object.__setattr__(self, "admissible_failures", self.failure_alphabet)
        else:
            object.__setattr__(
                self, "admissible_failures", frozenset(self.admissible_failures)
            )
        if self.admissible_dependencies is None:
            object.__setattr__(
                self, "admissible_dependencies", self.dependency_alphabet
            )
        else:
            object.__setattr__(
                self,
                "admissible_dependencies",
                frozenset(self.admissible_dependencies),
            )

        admissible_failures = self.admissible_failures
        admissible_dependencies = self.admissible_dependencies
        assert admissible_failures is not None
        assert admissible_dependencies is not None
        if not admissible_failures <= self.failure_alphabet:
            raise ValueError("Admissible failures must belong to the failure alphabet.")
        if not admissible_dependencies <= self.dependency_alphabet:
            raise ValueError(
                "Admissible dependencies must belong to the dependency alphabet."
            )

        for budget, alphabet, name in (
            (self.evidence_budget, self.evidence_alphabet, "evidence"),
            (self.compute_budget, self.compute_alphabet, "compute"),
            (self.dependency_budget, self.dependency_alphabet, "dependency"),
        ):
            if budget is not None and not 0 <= budget <= len(alphabet):
                raise ValueError(
                    f"{name} budget must be between zero and its alphabet size."
                )
        if self.lease_ticks <= 0:
            raise ValueError("A lease must have a positive finite duration.")

    def initial_state(self) -> ControllerState:
        """Allocate the configured finite accounting state."""

        return ControllerState(
            evidence_budget=(
                len(self.evidence_alphabet)
                if self.evidence_budget is None
                else self.evidence_budget
            ),
            compute_budget=(
                len(self.compute_alphabet)
                if self.compute_budget is None
                else self.compute_budget
            ),
            dependency_budget=(
                len(self.dependency_alphabet)
                if self.dependency_budget is None
                else self.dependency_budget
            ),
        )


@dataclass(frozen=True, slots=True)
class ControllerEvent:
    kind: ControllerEventKind
    lease_id: str | None = None
    outcome: ResolutionOutcome | None = None
    dependency: str | None = None
    clock: int | None = None


Payload: TypeAlias = str | Needed | None


@dataclass(frozen=True, slots=True)
class EvaluationReceipt:
    before: ControllerState
    verdict: Verdict
    payload: Payload
    dependency_arcs: frozenset[tuple[str, str]]
    action: ControllerAction
    after: ControllerState
    evaluation_index: int


@dataclass(frozen=True, slots=True)
class EventReceipt:
    before: ControllerState
    event: ControllerEvent
    action: ControllerAction
    after: ControllerState


TransitionReceipt: TypeAlias = EvaluationReceipt | EventReceipt


@dataclass(frozen=True, slots=True)
class AuditResult:
    valid: bool
    errors: tuple[str, ...]
    evaluations: int
    initial_rank: int
    final_rank: int


class QICTController:
    """Stateful facade over the pure transition functions."""

    def __init__(self, config: QICTConfig) -> None:
        self.config = config
        self.state = config.initial_state()
        self.receipts: list[TransitionReceipt] = []

    def process(
        self,
        result: CheckResult,
        *,
        dependency_arcs: Iterable[tuple[str, str]] = (),
    ) -> EvaluationReceipt:
        """Consume exactly one evaluator verdict and payload."""

        receipt = _evaluation_step(
            self.config,
            self.state,
            result,
            frozenset(dependency_arcs),
        )
        self.state = receipt.after
        self.receipts.append(receipt)
        return receipt

    def evidence_returned(self, lease_id: str) -> EventReceipt:
        return self._event(
            ControllerEvent(ControllerEventKind.EVIDENCE_RETURNED, lease_id=lease_id)
        )

    def resolve_dependency(
        self, lease_id: str, outcome: ResolutionOutcome
    ) -> EventReceipt:
        return self._event(
            ControllerEvent(
                ControllerEventKind.DEPENDENCY_RESOLVED,
                lease_id=lease_id,
                outcome=outcome,
            )
        )

    def advance_clock(self, clock: int) -> EventReceipt:
        return self._event(
            ControllerEvent(ControllerEventKind.ADVANCE_CLOCK, clock=clock)
        )

    def expire(self, lease_id: str) -> EventReceipt:
        return self._event(
            ControllerEvent(ControllerEventKind.EXPIRE, lease_id=lease_id)
        )

    def invalidate_dependency(self, dependency: str) -> EventReceipt:
        return self._event(
            ControllerEvent(
                ControllerEventKind.INVALIDATE_DEPENDENCY,
                dependency=dependency,
            )
        )

    def audit(self) -> AuditResult:
        """Replay and audit every receipt emitted so far."""

        return audit_trace(self.config, self.receipts)

    def _event(self, event: ControllerEvent) -> EventReceipt:
        receipt = _event_step(self.config, self.state, event)
        self.state = receipt.after
        self.receipts.append(receipt)
        return receipt


def rank(config: QICTConfig, state: ControllerState) -> int:
    """Return rho for the supplied accounting state."""

    return (
        len(config.failure_alphabet - state.exclusions)
        + state.evidence_budget
        + state.compute_budget
        + state.dependency_budget
    )


def audit_trace(
    config: QICTConfig,
    receipts: Iterable[TransitionReceipt],
    *,
    initial_state: ControllerState | None = None,
) -> AuditResult:
    """Replay receipts and verify state continuity, actions, and rank descent."""

    receipt_list = list(receipts)
    state = initial_state or config.initial_state()
    initial_rank = rank(config, state)
    errors: list[str] = []
    evaluations = 0

    for index, receipt in enumerate(receipt_list, start=1):
        if receipt.before != state:
            errors.append(f"receipt {index}: before-state breaks the receipt chain")
            state = receipt.after
            continue
        try:
            if isinstance(receipt, EvaluationReceipt):
                expected = _evaluation_step(
                    config,
                    state,
                    _result_from_receipt(receipt),
                    receipt.dependency_arcs,
                )
                evaluations += 1
                if receipt != expected:
                    errors.append(
                        f"receipt {index}: evaluation transition does not replay"
                    )
                if receipt.action in _RANK_CONSUMING_ACTIONS:
                    if receipt.after.status != ControllerStatus.RUNNING:
                        errors.append(
                            f"receipt {index}: rank-consuming transition terminated"
                        )
                    if rank(config, receipt.after) != rank(config, receipt.before) - 1:
                        errors.append(
                            f"receipt {index}: continuing transition did not reduce rho"
                        )
                elif receipt.after.status == ControllerStatus.RUNNING:
                    errors.append(
                        f"receipt {index}: evaluation neither terminated nor reduced rho"
                    )
            else:
                expected_event = _event_step(config, state, receipt.event)
                if receipt != expected_event:
                    errors.append(f"receipt {index}: controller event does not replay")
            state = receipt.after
        except (TypeError, ValueError) as exc:
            errors.append(f"receipt {index}: replay raised {exc}")
            state = receipt.after

    if evaluations > initial_rank + 1:
        errors.append("evaluation count exceeds rho_0 + 1")
    return AuditResult(
        valid=not errors,
        errors=tuple(errors),
        evaluations=evaluations,
        initial_rank=initial_rank,
        final_rank=rank(config, state),
    )


_RANK_CONSUMING_ACTIONS = frozenset(
    {
        ControllerAction.EXCLUDE_RETRY,
        ControllerAction.ADMIT_NEXT,
        ControllerAction.GATHER,
        ControllerAction.BIND,
    }
)


def _evaluation_step(
    config: QICTConfig,
    state: ControllerState,
    result: CheckResult,
    dependency_arcs: frozenset[tuple[str, str]],
) -> EvaluationReceipt:
    if state.status != ControllerStatus.RUNNING:
        raise ValueError("A terminal controller cannot evaluate another candidate.")
    if state.pending_lease is not None:
        raise ValueError("A pending lease must return or expire before re-evaluation.")

    before = state
    next_index = state.evaluation_count + 1
    base = replace(
        state,
        evaluation_count=next_index,
        candidate_disposition=None,
        held_reason=None,
    )
    payload = _payload(result)

    if result.verdict == Verdict.YES:
        after = replace(
            base,
            status=ControllerStatus.ACCEPTED,
            candidate_disposition=CandidateDisposition.TERMINALLY_ACCEPTED,
        )
        action = ControllerAction.EXIT_ACCEPT
    elif result.verdict == Verdict.NO:
        failure = result.reason
        assert failure is not None
        if (
            failure in config.failure_alphabet
            and failure not in state.exclusions
            and failure in _admissible_failures(config)
        ):
            after = replace(base, exclusions=state.exclusions | {failure})
            action = ControllerAction.EXCLUDE_RETRY
        else:
            after = _held(base, f"inadmissible or repeated failure: {failure}")
            action = ControllerAction.HOLD
    elif result.verdict == Verdict.MAYBE:
        needed = result.needed
        if isinstance(needed, EvidenceNeed):
            after, action = _gather(config, base, needed)
        elif isinstance(needed, ComputeNeed):
            after, action = _admit_next(config, base, needed)
        else:
            after = _held(base, "finite-rank control requires a typed MAYBE payload")
            action = ControllerAction.HOLD
    elif result.verdict == Verdict.IFF:
        dependency = result.dependency
        assert dependency is not None
        after, action = _bind(config, base, dependency, dependency_arcs)
    else:  # pragma: no cover - Verdict is a closed IntEnum
        after = _held(base, "unknown verdict")
        action = ControllerAction.HOLD

    return EvaluationReceipt(
        before=before,
        verdict=result.verdict,
        payload=payload,
        dependency_arcs=dependency_arcs,
        action=action,
        after=after,
        evaluation_index=next_index,
    )


def _event_step(
    config: QICTConfig,
    state: ControllerState,
    event: ControllerEvent,
) -> EventReceipt:
    del config  # Event transitions do not inspect alphabets or change rho.
    before = state
    if state.status != ControllerStatus.RUNNING:
        raise ValueError("A terminal controller cannot consume another event.")

    if event.kind == ControllerEventKind.INVALIDATE_DEPENDENCY:
        dependency = event.dependency
        if dependency is None or state.dependencies.outcome_for(dependency) is None:
            after = _held(state, "dependency invalidation has no prior resolution")
        else:
            after = _held(state, f"resolved dependency invalidated: {dependency}")
        return EventReceipt(before, event, ControllerAction.HOLD, after)

    lease = state.pending_lease
    if lease is None:
        after = _held(state, "controller event has no pending lease")
        return EventReceipt(before, event, ControllerAction.HOLD, after)

    if event.kind == ControllerEventKind.ADVANCE_CLOCK:
        if event.clock is None or event.clock <= state.clock:
            after = _held(state, "clock must advance strictly")
            action = ControllerAction.HOLD
        elif event.clock >= lease.deadline:
            after = _held(
                replace(state, clock=event.clock, pending_lease=None), "lease expired"
            )
            action = ControllerAction.EXPIRE
        else:
            after = replace(state, clock=event.clock)
            action = ControllerAction.WAIT
        return EventReceipt(before, event, action, after)

    if event.lease_id != lease.lease_id:
        after = _held(state, "controller event names the wrong lease")
        return EventReceipt(before, event, ControllerAction.HOLD, after)

    if event.kind == ControllerEventKind.EXPIRE:
        after = _held(replace(state, pending_lease=None), "lease expired")
        return EventReceipt(before, event, ControllerAction.EXPIRE, after)

    if event.kind == ControllerEventKind.EVIDENCE_RETURNED:
        if lease.kind != LeaseKind.EVIDENCE:
            after = _held(state, "evidence returned for a dependency lease")
            action = ControllerAction.HOLD
        else:
            after = replace(state, pending_lease=None)
            action = ControllerAction.REEVALUATE
        return EventReceipt(before, event, action, after)

    if event.kind == ControllerEventKind.DEPENDENCY_RESOLVED:
        if lease.kind != LeaseKind.DEPENDENCY or event.outcome is None:
            after = _held(state, "invalid dependency resolution event")
            action = ControllerAction.HOLD
        else:
            dependency = lease.obligation
            record = state.dependencies
            remaining = record.unresolved - {dependency}
            remaining_relation = frozenset(
                (source, target)
                for source, target in record.relation
                if source != dependency and target != dependency
            )
            outcomes = tuple(
                sorted(
                    (*record.outcomes, (dependency, event.outcome)),
                    key=lambda item: item[0],
                )
            )
            dependencies = DependencyRecord(
                used=record.used,
                unresolved=remaining,
                relation=remaining_relation,
                outcomes=outcomes,
            )
            after = replace(
                state,
                dependencies=dependencies,
                pending_lease=None,
                candidate_disposition=CandidateDisposition.SOURCE_ADMISSIBLE,
            )
            action = ControllerAction.REEVALUATE
        return EventReceipt(before, event, action, after)

    after = _held(state, "unsupported controller event")
    return EventReceipt(before, event, ControllerAction.HOLD, after)


def _gather(
    config: QICTConfig,
    state: ControllerState,
    needed: EvidenceNeed,
) -> tuple[ControllerState, ControllerAction]:
    obligation = needed.evidence
    if (
        obligation not in config.evidence_alphabet
        or obligation in state.used_evidence
        or state.evidence_budget <= 0
    ):
        return (
            _held(state, f"inadmissible or repeated evidence need: {obligation}"),
            ControllerAction.HOLD,
        )
    lease = Lease(
        lease_id=f"evidence:{obligation}:{state.evaluation_count}",
        kind=LeaseKind.EVIDENCE,
        obligation=obligation,
        deadline=state.clock + config.lease_ticks,
    )
    return (
        replace(
            state,
            used_evidence=state.used_evidence | {obligation},
            evidence_budget=state.evidence_budget - 1,
            pending_lease=lease,
        ),
        ControllerAction.GATHER,
    )


def _admit_next(
    config: QICTConfig,
    state: ControllerState,
    needed: ComputeNeed,
) -> tuple[ControllerState, ControllerAction]:
    obligation = needed.step
    if (
        obligation not in config.compute_alphabet
        or obligation in state.used_compute
        or state.compute_budget <= 0
    ):
        return (
            _held(state, f"inadmissible or repeated compute need: {obligation}"),
            ControllerAction.HOLD,
        )
    return (
        replace(
            state,
            used_compute=state.used_compute | {obligation},
            compute_budget=state.compute_budget - 1,
            candidate_disposition=CandidateDisposition.SOURCE_ADMISSIBLE,
        ),
        ControllerAction.ADMIT_NEXT,
    )


def _bind(
    config: QICTConfig,
    state: ControllerState,
    dependency: str,
    dependency_arcs: frozenset[tuple[str, str]],
) -> tuple[ControllerState, ControllerAction]:
    record = state.dependencies
    if (
        dependency not in config.dependency_alphabet
        or dependency in record.used
        or dependency not in _admissible_dependencies(config)
        or state.dependency_budget <= 0
    ):
        return (
            _held(state, f"inadmissible or repeated dependency: {dependency}"),
            ControllerAction.HOLD,
        )

    unresolved = record.unresolved | {dependency}
    relation = record.relation | dependency_arcs
    try:
        dependencies = DependencyRecord(
            used=record.used | {dependency},
            unresolved=unresolved,
            relation=relation,
            outcomes=record.outcomes,
        )
    except ValueError as exc:
        return _held(state, str(exc)), ControllerAction.HOLD

    lease = Lease(
        lease_id=f"dependency:{dependency}:{state.evaluation_count}",
        kind=LeaseKind.DEPENDENCY,
        obligation=dependency,
        deadline=state.clock + config.lease_ticks,
    )
    return (
        replace(
            state,
            dependencies=dependencies,
            dependency_budget=state.dependency_budget - 1,
            pending_lease=lease,
        ),
        ControllerAction.BIND,
    )


def _held(state: ControllerState, reason: str) -> ControllerState:
    return replace(
        state,
        status=ControllerStatus.HELD,
        pending_lease=None,
        held_reason=reason,
    )


def _payload(result: CheckResult) -> Payload:
    if result.verdict == Verdict.NO:
        return result.reason
    if result.verdict == Verdict.MAYBE:
        return result.needed
    if result.verdict == Verdict.IFF:
        return result.dependency
    return None


def _result_from_receipt(receipt: EvaluationReceipt) -> CheckResult:
    if receipt.verdict == Verdict.NO:
        return CheckResult(Verdict.NO, reason=_require_string(receipt.payload))
    if receipt.verdict == Verdict.YES:
        return CheckResult(Verdict.YES)
    if receipt.verdict == Verdict.MAYBE:
        needed = receipt.payload
        if not isinstance(needed, (str, EvidenceNeed, ComputeNeed)):
            raise TypeError("MAYBE receipt payload has the wrong type")
        return CheckResult(Verdict.MAYBE, needed=needed)
    return CheckResult(Verdict.IFF, dependency=_require_string(receipt.payload))


def _require_string(payload: Payload) -> str:
    if not isinstance(payload, str):
        raise TypeError("receipt payload must be a string")
    return payload


def _admissible_failures(config: QICTConfig) -> AbstractSet[str]:
    assert config.admissible_failures is not None
    return config.admissible_failures


def _admissible_dependencies(config: QICTConfig) -> AbstractSet[str]:
    assert config.admissible_dependencies is not None
    return config.admissible_dependencies


def _acyclic(vertices: AbstractSet[str], edges: AbstractSet[tuple[str, str]]) -> bool:
    adjacency: dict[str, set[str]] = {vertex: set() for vertex in vertices}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(vertex: str) -> bool:
        if vertex in visiting:
            return False
        if vertex in visited:
            return True
        visiting.add(vertex)
        for target in adjacency.get(vertex, ()):
            if not visit(target):
                return False
        visiting.remove(vertex)
        visited.add(vertex)
        return True

    return all(visit(vertex) for vertex in vertices)
