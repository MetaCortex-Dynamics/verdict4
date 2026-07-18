"""Tests for quaternary verdict core types and runner."""

from __future__ import annotations

import pytest

from quaternary import CheckResult, LoopOutcome, Verdict, iff, maybe, no, run, yes


# --- Core type tests ---


class TestVerdict:
    def test_values(self) -> None:
        assert Verdict.NO == 0
        assert Verdict.YES == 1
        assert Verdict.MAYBE == 2
        assert Verdict.IFF == 3

    def test_ordering(self) -> None:
        assert Verdict.NO < Verdict.YES < Verdict.MAYBE < Verdict.IFF


class TestCheckResult:
    def test_no_requires_reason(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            CheckResult(verdict=Verdict.NO)

    def test_maybe_requires_needed(self) -> None:
        with pytest.raises(ValueError, match="needed"):
            CheckResult(verdict=Verdict.MAYBE)

    def test_iff_requires_dependency(self) -> None:
        with pytest.raises(ValueError, match="dependency"):
            CheckResult(verdict=Verdict.IFF)

    def test_yes_no_requirements(self) -> None:
        r = yes()
        assert r.verdict == Verdict.YES

    def test_frozen(self) -> None:
        r = yes()
        with pytest.raises(AttributeError):
            r.verdict = Verdict.NO  # type: ignore[misc]


class TestConstructors:
    def test_no(self) -> None:
        r = no("bad input")
        assert r.verdict == Verdict.NO
        assert r.reason == "bad input"

    def test_yes(self) -> None:
        r = yes()
        assert r.verdict == Verdict.YES

    def test_maybe(self) -> None:
        r = maybe("redis connection")
        assert r.verdict == Verdict.MAYBE
        assert r.needed == "redis connection"

    def test_iff(self) -> None:
        r = iff("auth module exported")
        assert r.verdict == Verdict.IFF
        assert r.dependency == "auth module exported"

    def test_meta_passthrough(self) -> None:
        r = no("bad", file="test.py", line=42)
        assert r.meta == {"file": "test.py", "line": 42}


# --- Runner tests ---


class TestRunner:
    def test_immediate_yes(self) -> None:
        outcome = run(
            task="trivial",
            generate=lambda t, *, exclusions: "output",
            check=lambda o: yes(),
        )
        assert outcome.status == "accepted"
        assert outcome.rounds == 1
        assert outcome.exclusions == []

    def test_no_then_yes(self) -> None:
        calls = {"n": 0}

        def gen(task: str, *, exclusions: list[str]) -> str:
            calls["n"] += 1
            return f"v{calls['n']}"

        def chk(output: str) -> CheckResult:
            if output == "v1":
                return no("missing header")
            return yes()

        outcome = run(task="fix", generate=gen, check=chk)
        assert outcome.status == "accepted"
        assert outcome.rounds == 2
        assert "missing header" in outcome.exclusions

    def test_maybe_hold_without_gather(self) -> None:
        outcome = run(
            task="need evidence",
            generate=lambda t, *, exclusions: "output",
            check=lambda o: maybe("database connection"),
        )
        assert outcome.status == "held"
        assert outcome.held_reason == "database connection"

    def test_maybe_gather_resolves(self) -> None:
        outcome = run(
            task="need evidence",
            generate=lambda t, *, exclusions: "output",
            check=lambda o: maybe("schema file"),
            gather=lambda needed: {"schema": "found"},
            recheck=lambda o, e=None: yes(),
        )
        assert outcome.status == "accepted"

    def test_maybe_gather_still_maybe(self) -> None:
        """T6: second MAYBE after gather → hold, no infinite loop."""
        outcome = run(
            task="unresolvable",
            generate=lambda t, *, exclusions: "output",
            check=lambda o: maybe("external API"),
            gather=lambda needed: None,
            recheck=lambda o, e=None: maybe("external API still down"),
        )
        assert outcome.status == "held"

    def test_iff_resolved(self) -> None:
        outcome = run(
            task="conditional",
            generate=lambda t, *, exclusions: "output",
            check=lambda o: iff("auth ready"),
            recheck=lambda o, e=None: yes(),
            is_resolved=lambda d: True,
            is_failed=lambda d: False,
        )
        assert outcome.status == "accepted"

    def test_iff_blocked(self) -> None:
        outcome = run(
            task="blocked",
            generate=lambda t, *, exclusions: "output",
            check=lambda o: iff("deploy pipeline"),
            is_resolved=lambda d: False,
            is_failed=lambda d: False,
        )
        assert outcome.status == "blocked"
        assert outcome.blocked_by == "deploy pipeline"

    def test_max_rounds_exhaustion(self) -> None:
        outcome = run(
            task="never passes",
            generate=lambda t, *, exclusions: "bad",
            check=lambda o: no(f"failure #{len(exclusions) + 1}" if (exclusions := []) or True else no("")),
            max_rounds=3,
        )
        assert outcome.status == "exhausted"
        assert outcome.rounds == 3

    def test_exclusion_accumulation(self) -> None:
        """T7: exclusions accumulate and are returned."""
        calls = {"n": 0}

        def gen(task: str, *, exclusions: list[str]) -> str:
            calls["n"] += 1
            return f"v{calls['n']}"

        def chk(output: str) -> CheckResult:
            if output == "v1":
                return no("error A")
            if output == "v2":
                return no("error B")
            return yes()

        outcome = run(task="accumulate", generate=gen, check=chk)
        assert outcome.exclusions == ["error A", "error B"]
        assert outcome.status == "accepted"
        assert outcome.rounds == 3

    def test_contradiction_detection(self) -> None:
        """T8 (Cell C): contradictory exclusions flagged."""
        calls = {"n": 0}

        def gen(task: str, *, exclusions: list[str]) -> str:
            calls["n"] += 1
            return f"v{calls['n']}"

        def chk(output: str) -> CheckResult:
            if output == "v1":
                return no("use caching")
            if output == "v2":
                return no("not use caching")
            return yes()

        outcome = run(task="contradict", generate=gen, check=chk)
        assert outcome.status == "accepted"
        assert outcome.contradiction is not None
        assert len(outcome.contradiction) == 1
