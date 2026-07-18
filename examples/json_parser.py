"""
Worked example: JSON parser — binary vs quaternary convergence.

From: "Your Loop Has Two States. It Needs Four."
https://metacortexdynamics.substack.com/p/your-loop-has-two-states-it-needs

Simulates the JSON parser scenario from the post.
Binary loop: 3 full generations, oscillation.
Quaternary loop: 1 generation, 1 targeted repair, convergence.
"""

from __future__ import annotations

from quaternary import CheckResult, LoopOutcome, Verdict, iff, maybe, no, run, yes


# --- Simulated component state ---

class SimulatedParser:
    """Simulates a parser that initially fails on nested arrays
    and has a missing schema import."""

    def __init__(self) -> None:
        self.handles_nested_arrays = False
        self.schema_imported = False
        self.generation_count = 0

    def generate(self, task: str, *, exclusions: list[str]) -> dict[str, str]:
        self.generation_count += 1

        # After being told about the nested array issue, fix it
        if "nested array not handled" in exclusions:
            self.handles_nested_arrays = True

        return {
            "parser": "json_parser_v" + str(self.generation_count),
            "handles_nested": str(self.handles_nested_arrays),
            "schema_imported": str(self.schema_imported),
        }

    def check(self, output: dict[str, str]) -> CheckResult:
        """Evaluate each component."""
        # Test 1: basic parsing — always passes
        # Test 2: nested arrays
        if output["handles_nested"] == "False":
            return no("nested array not handled")

        # Validator: depends on schema import
        if output["schema_imported"] == "False":
            return iff("schema library imported")

        return yes()

    def is_resolved(self, dep: str) -> bool:
        if dep == "schema library imported":
            # Simulate: resolve by importing
            self.schema_imported = True
            return True
        return False

    def is_failed(self, dep: str) -> bool:
        return False

    def recheck(self, output: dict[str, str], evidence: object = None) -> CheckResult:
        # After dependency resolved, recheck
        if self.schema_imported and self.handles_nested_arrays:
            return yes()
        return no("still failing after dependency resolution")


def run_binary_simulation() -> None:
    """Simulate binary loop behavior from the post."""
    print("=" * 60)
    print("BINARY LOOP (simulated)")
    print("=" * 60)

    generations = 0
    for attempt in range(1, 4):
        generations += 1
        print(f"\nRound {attempt}: generate everything from scratch")
        if attempt == 1:
            print("  test 2 fails: nested arrays not handled")
            print("  -> regenerate everything")
        elif attempt == 2:
            print("  test 1 now fails (regression)")
            print("  -> regenerate everything")
        elif attempt == 3:
            print("  schema validator fails: missing import")
            print("  -> regenerate everything")

    print(f"\nResult: {generations} full generations, oscillation, no convergence")


def run_quaternary() -> None:
    """Run actual quaternary loop."""
    print("\n" + "=" * 60)
    print("QUATERNARY LOOP")
    print("=" * 60)

    sim = SimulatedParser()

    outcome: LoopOutcome = run(
        task="write json parser with schema validation",
        generate=sim.generate,
        check=sim.check,
        recheck=sim.recheck,
        is_resolved=sim.is_resolved,
        is_failed=sim.is_failed,
        max_rounds=5,
    )

    print(f"\nStatus: {outcome.status}")
    print(f"Rounds: {outcome.rounds}")
    print(f"Generations: {sim.generation_count}")
    print(f"Exclusions accumulated: {outcome.exclusions}")
    if outcome.contradiction:
        print(f"Contradictions flagged: {outcome.contradiction}")
    print(f"Output: {outcome.output}")


if __name__ == "__main__":
    run_binary_simulation()
    run_quaternary()
