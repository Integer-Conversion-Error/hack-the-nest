# Agent Loop

The pipeline resumes from saved artifacts and repairs only the findings that
invalidate them. Reviews and solver runs are reusable when their input and policy
fingerprints match. Changing the problem, plan, source, model, or review contract
invalidates the affected approval. All model calls share the configured
[provider, request budgets, and usage ledger](backend/MODEL_PROVIDERS.md).

## Init

1. Initialize a new working directory.
2. Parse the prompt into a typed problem statement and check it against the original request before planning. Apply scoped edits if interpretation needs repair.

## Plan

1. Think through the problem.
2. Research with the configured provider's grounded search. Reuse compatible research, saved clarifications, and the previous draft when revising a plan.
3. Evaluate the plan.
   - Check the soft criteria for approach, semantics, and assumptions.
   - Check the qubit limit. If retrying, also check the build time limit from previous attempts. 
4. Write the plan to `PLAN.md`.
5. Decide which variables users can tweak. Aim for two or three variables and choose sensible defaults for each.
6. Verify fidelity before Build. Save the approval with its input and reviewer fingerprints; revise affected fields instead of regenerating the entire plan.

## Build

1. Review existing files first. Reuse their approval when inputs match; otherwise create or patch only the affected files. Paginate source reads and keep a bounded tool history with checkpoint notes and intact recent tool-call pairs.
2. When the agent thinks the implementation is done, compare it with `PLAN.md`.
   - If it matches, proceed to **Test**.
   - If it falls short, return to step 1 of **Build**.

## Test

1. Run `main.py` in a bounded worker, or reuse saved verified runs for matching solver inputs, settings, and execution environment. Check the saved results and circuit before reuse.
2. Evaluate the build result.
   - If it passes, continue to the semantic check.
   - If it fails, return to **Build** with the error.
3. Evaluate semantics by comparing the implementation with the problem statement. At this point, assume the plan already matches the problem statement.
   - If the implementation matches, proceed to **Final Go/No-Go**.
   - If it falls short, return to **Build** with the semantic issues found.
4. Reuse semantic approval only when both the run evidence and review policy match. For large implementations, the reviewer can request specific missing source ranges before deciding.

## Final Go/No-Go

1. Confirm the saved problem-to-prompt approval still covers the current inputs.
   - If it passes, continue to the plan check.
   - If it fails, return to the NLP parsing step in **Init**.
2. Confirm the saved plan-to-problem approval still covers the current inputs and reviewer.
   - If it passes, continue to the implementation check.
   - If it fails, return to **Plan**.
3. Confirm the implementation-to-plan approval covers the exact source that Test checked. Stop if inputs change during a review.
   - If it passes, proceed to **GO**.
   - If it fails, return to **Build**.
