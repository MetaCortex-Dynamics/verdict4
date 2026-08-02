# verdict4

Four-value evaluation for agentic AI loops.

**Replace pass/fail with NO / YES / MAYBE / IFF.**

Most agentic loops evaluate with a boolean. Pass or fail. That works when every non-pass condition is actually failure. But it is not. Some outputs cannot be evaluated yet because evidence is missing. Some outputs are correct only if another result holds. A boolean check compresses both into failure and destroys correct work.

This package provides a four-value `Verdict` enum, a structured `CheckResult`, and a loop runner that handles all four branches.

Read the full explanation: [Your Loop Has Two States. It Needs Four.](https://metacortexdynamics.substack.com/p/your-loop-has-two-states-it-needs)

## Install

Install directly from GitHub:

```bash
pip install git+https://github.com/MetaCortex-Dynamics/verdict4.git@v0.1.0
```

Or from source:

```bash
git clone https://github.com/MetaCortex-Dynamics/verdict4.git
cd verdict4
pip install -e .
```

> **Note:** `verdict4` is not yet published to PyPI, so `pip install verdict4` does not work yet. Use the GitHub install above. A PyPI release is planned.

## Quick start

```python
from quaternary import Verdict, CheckResult, no, yes, maybe, iff, run

# Define your check function — return a CheckResult, not a bool
def check(output):
    if not output.get("valid"):
        return no("validation failed: missing required field")
    if not output.get("schema_available"):
        return maybe("schema file not found")
    if output.get("depends_on_auth"):
        return iff("auth module deployed")
    return yes()

# Define your generator
def generate(task, *, exclusions):
    # Your agent generates output here
    # `exclusions` carries named reasons from prior NO results
    return {"valid": True, "schema_available": True}

# Run the loop
outcome = run(
    task="build parser",
    generate=generate,
    check=check,
)

print(outcome.status)       # "accepted" | "held" | "blocked" | "exhausted"
print(outcome.exclusions)   # accumulated NO reasons
print(outcome.rounds)       # how many iterations
```

## The four verdicts

| Verdict | Meaning | Loop action |
|---------|---------|-------------|
| `NO`    | Fails a named condition | Add reason to exclusions, retry |
| `YES`   | Satisfies conditions | Accept |
| `MAYBE` | Cannot evaluate — evidence missing | Hold, gather evidence, recheck |
| `IFF`   | Correct if dependency holds | Bind to dependency, resolve, recheck |

## API

### Core types

```python
from quaternary import Verdict, CheckResult, no, yes, maybe, iff
```

**`Verdict`** — `IntEnum` with values `NO=0`, `YES=1`, `MAYBE=2`, `IFF=3`

**`CheckResult`** — frozen dataclass:
- `verdict: Verdict`
- `reason: str | None` — required for `NO`
- `needed: str | None` — required for `MAYBE`
- `dependency: str | None` — required for `IFF`
- `meta: dict[str, Any]` — optional domain metadata

**Constructors**: `no(reason)`, `yes()`, `maybe(needed)`, `iff(dependency)` — all accept `**meta` kwargs.

### Runner

```python
from quaternary import run, LoopOutcome
```

**`run(task, *, generate, check, ...)`** — runs the quaternary loop.

Required:
- `generate(task, *, exclusions) -> output`
- `check(output) -> CheckResult`

Optional:
- `gather(needed) -> evidence` — called on MAYBE
- `recheck(output, evidence?) -> CheckResult` — called after gather or dependency resolution
- `is_resolved(dep) -> bool` — called on IFF
- `is_failed(dep) -> bool` — called on IFF
- `max_rounds: int` — default 10
- `on_hold(output, reason)` — callback when holding
- `on_blocked(output, dep)` — callback when blocked

**`LoopOutcome`**:
- `status`: `"accepted"` | `"held"` | `"blocked"` | `"exhausted"`
- `output`: the final output (or None if exhausted)
- `exclusions`: accumulated NO reasons
- `rounds`: iterations used
- `held_reason` / `blocked_by`: why the loop stopped
- `contradiction`: flagged contradictory exclusion pairs (if any)

## Examples

```bash
python examples/json_parser.py
python examples/multi_file_refactor.py
```

## Design constraints

- **Zero domain logic.** The runner is a protocol over user-supplied callables.
- **No framework.** The fix is local — an enum and a branch.
- **Bounded MAYBE.** A second MAYBE after evidence gathering → hold and report. No infinite gather loops.
- **Exclusion memory.** NO reasons accumulate across rounds. Contradictions are flagged, not resolved.
- **No dependencies.** Standard library only.

## License

MIT — [MetaCortex Dynamics](https://github.com/MetaCortex-Dynamics)
