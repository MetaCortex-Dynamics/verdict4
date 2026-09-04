# SPDX-License-Identifier: MIT
"""Executable checks for the finite-rank controller contract."""

from __future__ import annotations

import random
from dataclasses import fields, replace

import pytest

from quaternary import (
    CandidateDisposition,
    ComputeNeed,
    ControllerAction,
    ControllerState,
    ControllerStatus,
    DependencyRecord,
    EvidenceNeed,
    LeaseKind,
    QICTConfig,
    QICTController,
    ResolutionOutcome,
    audit_trace,
    iff,
    maybe,
    no,
    rank,
    yes,
)


def test_needed_is_a_disjoint_runtime_sum() -> None:
    evidence = EvidenceNeed("schema")
    compute = ComputeNeed("next_1")

    assert evidence != compute
    assert maybe(evidence).needed == evidence
    assert maybe(compute).needed == compute
    assert maybe("legacy evidence").needed == "legacy evidence"


def test_controller_fails_closed_on_legacy_untyped_maybe() -> None:
    controller = QICTController(QICTConfig(evidence_alphabet={"schema"}))

    receipt = controller.process(maybe("schema"))

    assert receipt.action == ControllerAction.HOLD
    assert controller.state.status == ControllerStatus.HELD


def test_source_admission_is_not_terminal_acceptance() -> None:
    controller = QICTController(QICTConfig(compute_alphabet={"next_1"}))

    compute = controller.process(maybe(ComputeNeed("next_1")))

    assert compute.action == ControllerAction.ADMIT_NEXT
    assert compute.after.status == ControllerStatus.RUNNING
    assert compute.after.candidate_disposition == CandidateDisposition.SOURCE_ADMISSIBLE

    accepted = controller.process(yes())

    assert accepted.action == ControllerAction.EXIT_ACCEPT
    assert accepted.after.status == ControllerStatus.ACCEPTED
    assert (
        accepted.after.candidate_disposition == CandidateDisposition.TERMINALLY_ACCEPTED
    )


@pytest.mark.parametrize("loop_applications", range(1, 25))
def test_fixed_count_corollary_is_exact(loop_applications: int) -> None:
    compute_alphabet = {f"next_{index}" for index in range(1, loop_applications)}
    config = QICTConfig(compute_alphabet=compute_alphabet)
    controller = QICTController(config)
    rho_0 = rank(config, controller.state)
    k = 1

    for index in range(1, loop_applications):
        receipt = controller.process(maybe(ComputeNeed(f"next_{index}")))
        assert receipt.action == ControllerAction.ADMIT_NEXT
        k += 1
    controller.process(yes())

    q = controller.state.evaluation_count
    assert k == q == loop_applications
    assert q == rho_0 + 1
    assert controller.audit().valid


def test_second_gather_on_same_obligation_holds() -> None:
    config = QICTConfig(evidence_alphabet={"schema"}, lease_ticks=2)
    controller = QICTController(config)

    first = controller.process(maybe(EvidenceNeed("schema")))
    assert first.action == ControllerAction.GATHER
    lease = first.after.pending_lease
    assert lease is not None
    controller.evidence_returned(lease.lease_id)

    second = controller.process(maybe(EvidenceNeed("schema")))

    assert second.action == ControllerAction.HOLD
    assert second.after.status == ControllerStatus.HELD
    assert controller.audit().valid


def test_dependency_is_one_use_after_resolution() -> None:
    config = QICTConfig(dependency_alphabet={"branch_ready"}, lease_ticks=2)
    controller = QICTController(config)

    first = controller.process(iff("branch_ready"))
    assert first.action == ControllerAction.BIND
    lease = first.after.pending_lease
    assert lease is not None and lease.kind == LeaseKind.DEPENDENCY
    resolved = controller.resolve_dependency(lease.lease_id, ResolutionOutcome.HOLDS)
    assert resolved.action == ControllerAction.REEVALUATE
    assert (
        controller.state.dependencies.outcome_for("branch_ready")
        == ResolutionOutcome.HOLDS
    )

    second = controller.process(iff("branch_ready"))

    assert second.action == ControllerAction.HOLD
    assert second.after.status == ControllerStatus.HELD
    assert controller.audit().valid


def test_later_dependency_invalidation_holds_without_rebinding() -> None:
    controller = QICTController(
        QICTConfig(dependency_alphabet={"branch_ready"}, lease_ticks=2)
    )
    bound = controller.process(iff("branch_ready"))
    lease = bound.after.pending_lease
    assert lease is not None
    controller.resolve_dependency(lease.lease_id, ResolutionOutcome.HOLDS)

    invalidated = controller.invalidate_dependency("branch_ready")

    assert invalidated.action == ControllerAction.HOLD
    assert invalidated.after.status == ControllerStatus.HELD
    assert invalidated.after.dependencies.used == {"branch_ready"}


def test_self_cyclic_dependency_holds() -> None:
    controller = QICTController(QICTConfig(dependency_alphabet={"delta"}))

    receipt = controller.process(iff("delta"), dependency_arcs={("delta", "delta")})

    assert receipt.action == ControllerAction.HOLD
    assert receipt.after.status == ControllerStatus.HELD


def test_lease_expiry_is_a_logical_event_not_an_evaluation() -> None:
    config = QICTConfig(evidence_alphabet={"schema"}, lease_ticks=3)
    controller = QICTController(config)
    dispatched = controller.process(maybe(EvidenceNeed("schema")))
    lease = dispatched.after.pending_lease
    assert lease is not None

    waiting = controller.advance_clock(lease.deadline - 1)
    expired = controller.advance_clock(lease.deadline)

    assert waiting.action == ControllerAction.WAIT
    assert expired.action == ControllerAction.EXPIRE
    assert expired.after.status == ControllerStatus.HELD
    assert expired.after.evaluation_count == 1
    assert rank(config, expired.after) == 0
    assert controller.audit().valid


def test_explicit_expiry_holds() -> None:
    controller = QICTController(
        QICTConfig(dependency_alphabet={"dependency"}, lease_ticks=5)
    )
    dispatched = controller.process(iff("dependency"))
    lease = dispatched.after.pending_lease
    assert lease is not None

    expired = controller.expire(lease.lease_id)

    assert expired.action == ControllerAction.EXPIRE
    assert expired.after.status == ControllerStatus.HELD
    assert expired.after.evaluation_count == 1


def test_clock_event_must_advance_strictly() -> None:
    controller = QICTController(QICTConfig(evidence_alphabet={"schema"}, lease_ticks=3))
    controller.process(maybe(EvidenceNeed("schema")))

    receipt = controller.advance_clock(0)

    assert receipt.action == ControllerAction.HOLD
    assert receipt.after.status == ControllerStatus.HELD


def test_failure_exclusion_is_exact_and_one_use() -> None:
    config = QICTConfig(failure_alphabet={"mem_oob"})
    controller = QICTController(config)

    first = controller.process(no("mem_oob"))
    second = controller.process(no("mem_oob"))

    assert first.action == ControllerAction.EXCLUDE_RETRY
    assert first.after.exclusions == {"mem_oob"}
    assert second.action == ControllerAction.HOLD
    assert controller.audit().valid


def test_inadmissible_failure_holds() -> None:
    controller = QICTController(
        QICTConfig(
            failure_alphabet={"safe", "unsafe"},
            admissible_failures={"safe"},
        )
    )

    receipt = controller.process(no("unsafe"))

    assert receipt.action == ControllerAction.HOLD
    assert receipt.after.exclusions == frozenset()


def test_receipt_accounting_state_contains_no_candidate_activation() -> None:
    field_names = {item.name for item in fields(ControllerState)}

    assert "candidate" not in field_names
    assert "candidate_activation" not in field_names
    assert "hidden_state" not in field_names


def test_receipt_audit_detects_semantic_tampering() -> None:
    config = QICTConfig(compute_alphabet={"next_1"})
    controller = QICTController(config)
    receipt = controller.process(maybe(ComputeNeed("next_1")))
    tampered = replace(receipt, action=ControllerAction.EXIT_ACCEPT)

    audit = audit_trace(config, [tampered])

    assert not audit.valid
    assert any("does not replay" in error for error in audit.errors)


def test_adversarial_always_no_uses_every_failure_then_holds() -> None:
    failures = ("f0", "f1", "f2", "f3")
    config = QICTConfig(failure_alphabet=failures)
    controller = QICTController(config)

    for failure in failures:
        receipt = controller.process(no(failure))
        assert receipt.action == ControllerAction.EXCLUDE_RETRY
    terminal = controller.process(no(failures[0]))

    assert terminal.action == ControllerAction.HOLD
    assert terminal.after.evaluation_count == len(failures) + 1
    assert terminal.after.evaluation_count == rank(config, config.initial_state()) + 1
    assert controller.audit().valid


def test_adversarial_evidence_cycle_holds_on_second_use() -> None:
    config = QICTConfig(evidence_alphabet={"g"}, lease_ticks=2)
    controller = QICTController(config)
    first = controller.process(maybe(EvidenceNeed("g")))
    lease = first.after.pending_lease
    assert lease is not None
    controller.evidence_returned(lease.lease_id)

    terminal = controller.process(maybe(EvidenceNeed("g")))

    assert terminal.action == ControllerAction.HOLD
    assert terminal.after.evaluation_count == 2
    assert controller.audit().valid


def test_adversarial_iff_chain_cannot_close_a_cycle() -> None:
    config = QICTConfig(dependency_alphabet={"a", "b"})
    controller = QICTController(config)
    controller.state = replace(
        config.initial_state(),
        dependencies=DependencyRecord(
            used=frozenset({"a"}), unresolved=frozenset({"a"})
        ),
        dependency_budget=1,
    )

    receipt = controller.process(iff("b"), dependency_arcs={("a", "b"), ("b", "a")})

    assert receipt.action == ControllerAction.HOLD
    assert receipt.after.status == ControllerStatus.HELD


def test_adversarial_yes_never_terminates_at_rank_plus_one() -> None:
    config = QICTConfig(
        failure_alphabet={"f0", "f1"},
        compute_alphabet={"next_1", "next_2"},
    )
    controller = QICTController(config)
    rho_0 = rank(config, controller.state)

    controller.process(no("f0"))
    controller.process(maybe(ComputeNeed("next_1")))
    controller.process(no("f1"))
    controller.process(maybe(ComputeNeed("next_2")))
    terminal = controller.process(no("f0"))

    assert terminal.after.status == ControllerStatus.HELD
    assert terminal.after.evaluation_count == rho_0 + 1
    assert controller.audit().valid


def test_out_of_alphabet_compute_need_holds_without_consuming_rank() -> None:
    config = QICTConfig(compute_alphabet={"next_1"})
    controller = QICTController(config)
    rho_0 = rank(config, controller.state)

    receipt = controller.process(maybe(ComputeNeed("next_2")))

    assert receipt.action == ControllerAction.HOLD
    assert rank(config, receipt.after) == rho_0


def test_wrong_lease_event_holds_and_replays() -> None:
    controller = QICTController(QICTConfig(evidence_alphabet={"g"}, lease_ticks=2))
    controller.process(maybe(EvidenceNeed("g")))

    receipt = controller.evidence_returned("not-the-lease")

    assert receipt.action == ControllerAction.HOLD
    assert controller.audit().valid


def test_randomized_sound_and_unsound_evaluators_obey_rank_bound() -> None:
    rng = random.Random(0x51554354)

    for _ in range(400):
        config = QICTConfig(
            failure_alphabet={"f0", "f1", "f2"},
            evidence_alphabet={"g0", "g1"},
            compute_alphabet={"k0", "k1"},
            dependency_alphabet={"d0", "d1"},
            lease_ticks=2,
        )
        controller = QICTController(config)
        rho_0 = rank(config, controller.state)

        while controller.state.status == ControllerStatus.RUNNING:
            lease = controller.state.pending_lease
            if lease is not None:
                event_choice = rng.randrange(4)
                if event_choice == 0:
                    controller.expire(lease.lease_id)
                elif event_choice == 1:
                    controller.advance_clock(lease.deadline)
                elif lease.kind == LeaseKind.EVIDENCE:
                    controller.evidence_returned(lease.lease_id)
                else:
                    outcome = rng.choice(tuple(ResolutionOutcome))
                    controller.resolve_dependency(lease.lease_id, outcome)
                continue

            choice = rng.randrange(10)
            if choice == 0:
                result = yes()
                arcs: set[tuple[str, str]] = set()
            elif choice in (1, 2):
                result = no(rng.choice(("f0", "f1", "f2", "outside")))
                arcs = set()
            elif choice in (3, 4):
                result = maybe(EvidenceNeed(rng.choice(("g0", "g1", "outside"))))
                arcs = set()
            elif choice in (5, 6):
                result = maybe(ComputeNeed(rng.choice(("k0", "k1", "outside"))))
                arcs = set()
            elif choice == 7:
                result = maybe("untyped")
                arcs = set()
            else:
                dependency = rng.choice(("d0", "d1", "outside"))
                result = iff(dependency)
                arcs = {(dependency, dependency)} if choice == 9 else set()

            controller.process(result, dependency_arcs=arcs)

        audit = controller.audit()
        assert audit.valid, audit.errors
        assert controller.state.evaluation_count <= rho_0 + 1
