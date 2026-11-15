---
name: run-quantum-solver
description: Build and execute a circuit for an approved plan through the bounded Python worker. Use when preparing a solver attempt, running Quantum Rings, or diagnosing an execution failure.
---

# Run a Quantum Solver

Use the current `RunRequest`, `PLAN.md`, and the host's worker, limits, and workspace. Keep every execution associated with the attempt that supplied its inputs.

1. Validate requested inputs and the bit-to-entity mapping against the plan. Use the reviewed circuit builder. Draft changes in the run workspace and execute arbitrary generated Python only in an isolated worker supplied by the host.
2. Check the width of the circuit that will run, including auxiliary qubits, and verify the effective shot count and remaining time. Record the preflight failure when the plan cannot run within its limits.
3. Run the local Python worker with the configured deadline. Send one versioned JSON request to its stdin and collect one result from stdout. The worker writes diagnostics to stderr. Clean up a timed-out worker before issuing another attempt.
4. Preserve effective inputs, circuit identity, simulator backend and settings, and raw measurement counts or their workspace reference. Validate execution status and count totals against the effective shots. Pass the result to verification for independent decoding, feasibility, and scoring.

Return `RunResult` with the attempt's execution evidence. For launch, build, or simulation failures, return the failing stage and actionable diagnostics to the pipeline agent for retry routing. Keep the request and result IDs from the host so stale or partial results can be identified.

See the [backend pipeline plan](../../../../backend-pipeline-plan.md) for budget defaults and the [architecture plan](../../../../system-architecture-plan.md) for the worker contract.
