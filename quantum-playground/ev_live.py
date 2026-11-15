"""Run the checked-in EV solver in a bounded process and build a live handoff."""

import json
import subprocess
import sys
from importlib.util import find_spec
from uuid import uuid4

from ev_recorded import LIVE_PROMPT, RECORDED_CONTROLS, RECORDED_PROMPT, REPO_ROOT, project_ev_result
from fleet_handoff import applied_values, normalize_controls

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ev_dispatch import DEFAULT_VAN2_CHARGE_PCT, VAN2_CHARGE_PCTS

CONTROL_ID = "van_2_initial_charge_pct"
LEGACY_PLAN_ID = "ev-fleet-dispatch-live-v1"
PLAN_ID = "ev-fleet-dispatch-live-v2"
LOCAL_PLAN_IDS = frozenset((LEGACY_PLAN_ID, PLAN_ID))
LIVE_CONTROLS = normalize_controls([{
    "id": CONTROL_ID,
    "label": "Van 2 starting charge",
    "type": "integer",
    "unit": "%",
    "default": DEFAULT_VAN2_CHARGE_PCT,
    "minimum": VAN2_CHARGE_PCTS.start,
    "maximum": VAN2_CHARGE_PCTS[-1],
    "step": VAN2_CHARGE_PCTS.step,
    "rerun_mode": "rebuild",
}])
SOLVER_TIMEOUT_SECONDS = 120


class SolverUnavailable(RuntimeError):
    pass


class SolverFailed(RuntimeError):
    pass


def local_solver_available():
    return all(find_spec(module) is not None for module in ("numpy", "scipy", "dotenv"))


def run_solver(charge):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.ev_dispatch", "--van-2-initial-charge", str(charge)],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SOLVER_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError("The EV solver timed out. Retry the selected charge.") from None
    except OSError:
        raise SolverFailed("The EV solver process could not start.") from None

    if result.returncode != 0:
        raise SolverFailed("The EV solver failed. Check the solver dependencies and Quantum Rings configuration.")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise SolverFailed("The EV solver returned invalid JSON.") from None


def local_handoff(prompt, values, previous, attempt_id):
    if previous is not None and previous["plan_id"] == LEGACY_PLAN_ID:
        expected_prompt, controls, plan_id = RECORDED_PROMPT, RECORDED_CONTROLS, LEGACY_PLAN_ID
    else:
        expected_prompt, controls, plan_id = LIVE_PROMPT, LIVE_CONTROLS, PLAN_ID
    if prompt != expected_prompt:
        raise ValueError("The built-in solver supports the EV example prompt.")
    if not local_solver_available():
        raise SolverUnavailable("Install quantum-playground/requirements.txt to enable the built-in solver.")

    defaults = {control["id"]: control["default"] for control in controls}
    selected = applied_values(controls, defaults, values)
    identity = {
        "run_id": previous["run_id"] if previous is not None else str(uuid4()),
        "plan_id": plan_id,
        "attempt_id": attempt_id,
    }
    try:
        return project_ev_result(
            run_solver(selected[CONTROL_ID]), identity, provenance="live",
            explanation="Verified against exact classical enumeration.",
            prompt=prompt, controls=controls,
        )
    except (KeyError, TypeError):
        raise SolverFailed("The EV solver returned incomplete schedule data.") from None
