"""Test steps 1 and 2: run main.py and evaluate the build result.

The solver runs in its own Python process, inside the workspace folder, with a
time limit. The run passes if it exits cleanly, writes a results.json that
follows the layout in prompts.md (build.contract), and saves circuit.png. Otherwise the issues and the
end of the program's output go back to Build.

    run = run_solver(workspace)                                       # plan defaults
    run = run_solver(workspace, {"van_2_initial_charge_pct": 20})     # another control value
    run.passed, run.issues, run.results
"""

import json
import os
import subprocess
import sys
import time

from pydantic import BaseModel

from backend.build import ENTRY_POINT
from backend.plan import PLAN_JSON, load_plan
from backend.prompt_loader import load_prompt
from backend.workspace import Workspace

RESULTS_JSON = "results.json"
CIRCUIT_DIAGRAM = "circuit.png"  # saved by qr_runtime.save_circuit_diagram
REQUIRED_KEYS = ("status", "controls", "objective", "solution", "reference", "quantum", "runtime_seconds")
OUTPUT_TAIL_LINES = 40


class RunOutcome(BaseModel):
    command: str
    controls: dict
    exit_code: int | None  # None when the run was stopped at the time limit
    timed_out: bool
    seconds: float
    stdout_tail: str
    stderr_tail: str
    results: dict | None
    issues: list[str]

    @property
    def passed(self) -> bool:
        return not self.issues

    def feedback(self) -> str:
        """The failure as text for the Build step."""
        return load_prompt(
            "test.run_failed",
            command=self.command,
            issues="\n".join(f"- {issue}" for issue in self.issues),
            stdout=self.stdout_tail or "(nothing printed)",
            stderr=self.stderr_tail or "(nothing printed)",
        )


def limits(workspace: Workspace) -> tuple[int, int]:
    """(max qubits, time limit in seconds) for this workspace: the plan's if there is one."""
    if workspace.resolve(PLAN_JSON).is_file():
        planning = load_plan(workspace)
        return planning.max_qubits, planning.worker_seconds
    meta = workspace.meta["limits"]
    return meta["max_qubits"], meta["run_timeout_seconds"]


def _tail(text: str | bytes | None) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return "\n".join((text or "").strip().splitlines()[-OUTPUT_TAIL_LINES:])


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _result_issues(results: dict, controls: dict, max_qubits: int, diagram_exists: bool) -> list[str]:
    """Check a results.json that was written and parsed."""
    issues = []
    missing = [key for key in REQUIRED_KEYS if key not in results]
    if missing:
        issues.append(f"{RESULTS_JSON} is missing the keys: {', '.join(missing)}.")
    if results.get("status") != "ok":
        issues.append(f"{RESULTS_JSON} reports status {results.get('status')!r}: {results.get('error')}")
        return issues

    objective = results.get("objective")
    if not isinstance(objective, dict) or not _is_number(objective.get("value")):
        issues.append(f"{RESULTS_JSON} needs objective.value as a number.")
    if not results.get("solution"):
        issues.append(f"{RESULTS_JSON} has an empty solution.")

    quantum = results.get("quantum")
    if not isinstance(quantum, dict):
        issues.append(f"{RESULTS_JSON} needs a quantum section.")
    else:
        qubits = quantum.get("qubits")
        if not isinstance(qubits, int) or qubits < 1:
            issues.append(f"{RESULTS_JSON} needs quantum.qubits as a positive whole number.")
        elif qubits > max_qubits:
            issues.append(f"The run used {qubits} qubits, above the limit of {max_qubits}.")
        if not isinstance(quantum.get("counts"), dict) or not quantum["counts"]:
            issues.append(f"{RESULTS_JSON} needs quantum.counts with the measured outcomes of the final circuit.")
        diagram = quantum.get("circuit_diagram")
        if not isinstance(diagram, dict) or diagram.get("file") != CIRCUIT_DIAGRAM or not diagram_exists:
            issues.append(
                f"The run must save {CIRCUIT_DIAGRAM} with qr_runtime.save_circuit_diagram(final_circuit, qubit_labels) "
                f"and put the returned dict in {RESULTS_JSON} under quantum.circuit_diagram."
            )

    used = results.get("controls")
    for key, value in controls.items():
        if not isinstance(used, dict) or used.get(key) != value:
            issues.append(f"The run was given --{key} {value}, but {RESULTS_JSON} reports controls {used!r}.")
    return issues


def run_solver(workspace: Workspace, controls: dict | None = None, timeout: int | None = None) -> RunOutcome:
    """Run the workspace's main.py and evaluate the build result. Never raises for a failed run."""
    controls = dict(controls or {})
    max_qubits, time_limit = limits(workspace)
    timeout = timeout or time_limit

    arguments = [ENTRY_POINT]
    for key, value in controls.items():
        arguments += [f"--{key}", str(value)]
    command = "python " + " ".join(arguments)

    results_path = workspace.resolve(RESULTS_JSON)
    diagram_path = workspace.resolve(CIRCUIT_DIAGRAM)
    results_path.unlink(missing_ok=True)  # never read the output of an earlier run
    diagram_path.unlink(missing_ok=True)

    if not workspace.resolve(ENTRY_POINT).is_file():
        return RunOutcome(
            command=command, controls=controls, exit_code=None, timed_out=False, seconds=0.0,
            stdout_tail="", stderr_tail="", results=None, issues=[f"{ENTRY_POINT} does not exist."],
        )

    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.monotonic()
    issues = []
    try:
        finished = subprocess.run(
            [sys.executable, *arguments],
            cwd=workspace.path, env=environment, timeout=timeout,
            capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        )
        exit_code, timed_out = finished.returncode, False
        stdout, stderr = finished.stdout, finished.stderr
    except subprocess.TimeoutExpired as expired:
        exit_code, timed_out = None, True
        stdout, stderr = expired.stdout, expired.stderr
        issues.append(f"The run did not finish within the {timeout}-second limit and was stopped.")
    seconds = round(time.monotonic() - started, 2)

    if exit_code not in (0, None):
        issues.append(f"The program exited with code {exit_code}.")

    results = None
    if results_path.is_file():
        try:
            results = json.loads(results_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            issues.append(f"{RESULTS_JSON} is not valid JSON: {error}")
        else:
            if isinstance(results, dict):
                issues += _result_issues(results, controls, max_qubits, diagram_path.is_file())
            else:
                issues.append(f"{RESULTS_JSON} must contain a JSON object.")
                results = None
    elif not timed_out:
        issues.append(f"The program did not write {RESULTS_JSON}.")

    return RunOutcome(
        command=command, controls=controls, exit_code=exit_code, timed_out=timed_out, seconds=seconds,
        stdout_tail=_tail(stdout), stderr_tail=_tail(stderr), results=results, issues=issues,
    )
