"""
Worked example: multi-file auth refactor — JWT to Paseto.

From: "Your Loop Has Two States. It Needs Four."
https://metacortexdynamics.substack.com/p/your-loop-has-two-states-it-needs

Demonstrates all four verdict states in a single evaluation pass:
    auth.py          -> YES
    middleware.py     -> NO  (still imports jwt.decode)
    routes.py        -> IFF (depends on auth.py export)
    test_auth.py     -> MAYBE (Redis unavailable)
"""

from __future__ import annotations

from quaternary import CheckResult, LoopOutcome, iff, maybe, no, run, yes


# --- Simulated file evaluation ---

FILES = ["auth.py", "middleware.py", "routes.py", "tests/test_auth.py"]


class RefactorSimulator:
    """Simulates per-file evaluation of a JWT->Paseto migration."""

    def __init__(self, redis_available: bool = False) -> None:
        self.redis_available = redis_available
        self.middleware_fixed = False

    def check(self, output: str) -> CheckResult:
        """Per-file quaternary evaluation."""
        if output == "auth.py":
            return yes(file="auth.py")

        elif output == "middleware.py":
            if not self.middleware_fixed:
                return no(
                    "still imports jwt.decode; should import paseto.parse",
                    file="middleware.py",
                )
            return yes(file="middleware.py")

        elif output == "routes.py":
            return iff(
                "auth.py exports new token type",
                file="routes.py",
            )

        elif output == "tests/test_auth.py":
            if not self.redis_available:
                return maybe(
                    "Redis instance for integration test mock",
                    file="tests/test_auth.py",
                )
            return yes(file="tests/test_auth.py")

        return no(f"unknown file: {output}")

    def gather(self, needed: str) -> dict[str, bool]:
        """Attempt to gather missing evidence."""
        if needed == "Redis instance for integration test mock":
            return {"redis_available": self.redis_available}
        return {}

    def recheck(self, output: str, evidence: object = None) -> CheckResult:
        """Recheck after evidence gathered or dependency resolved."""
        if output == "routes.py":
            # auth.py already exports — dependency satisfied
            return yes(file="routes.py")

        if output == "tests/test_auth.py":
            if not self.redis_available:
                return maybe(
                    "Redis instance for integration test mock",
                    file="tests/test_auth.py",
                )
            return yes(file="tests/test_auth.py")

        return self.check(output)

    def is_resolved(self, dep: str) -> bool:
        if dep == "auth.py exports new token type":
            return True  # auth.py already accepted
        return False

    def is_failed(self, dep: str) -> bool:
        return False


def run_all_files() -> None:
    """Evaluate each file through the quaternary loop independently.

    Each file gets its own loop. The shared simulator tracks
    cross-file state (middleware fix propagates via exclusions).
    """
    print("=" * 60)
    print("MULTI-FILE REFACTOR: JWT -> Paseto")
    print("=" * 60)

    sim = RefactorSimulator(redis_available=False)
    results: dict[str, LoopOutcome] = {}

    for filename in FILES:
        # For middleware: generate applies exclusion-driven repair
        def make_gen(f: str, s: RefactorSimulator) -> object:
            def gen(task: str, *, exclusions: list[str]) -> str:
                if "still imports jwt.decode; should import paseto.parse" in exclusions:
                    s.middleware_fixed = True
                return f
            return gen

        outcome = run(
            task=f"evaluate {filename}",
            generate=make_gen(filename, sim),  # type: ignore[arg-type]
            check=sim.check,
            gather=sim.gather,
            recheck=sim.recheck,
            is_resolved=sim.is_resolved,
            is_failed=sim.is_failed,
            max_rounds=3,
        )

        results[filename] = outcome
        print(f"\n{filename}:")
        print(f"  status: {outcome.status}")
        if outcome.exclusions:
            print(f"  exclusions: {outcome.exclusions}")
        if outcome.held_reason:
            print(f"  held because: {outcome.held_reason}")
        if outcome.blocked_by:
            print(f"  blocked by: {outcome.blocked_by}")
        print(f"  rounds: {outcome.rounds}")

    # Summary
    print("\n" + "-" * 60)
    accepted = [f for f, o in results.items() if o.status == "accepted"]
    held = [f for f, o in results.items() if o.status == "held"]
    print(f"Accepted:  {accepted}")
    print(f"Held:      {held}")
    print(f"Regenerated accepted files: 0")
    print(f"Oscillation: 0")


if __name__ == "__main__":
    run_all_files()
