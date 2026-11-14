# Agent Loop

## Init

1. Initialize a new working directory.
2. Parse the NLP into a problem statement.

## Plan

1. Think through the problem.
2. Research using ground search.
3. Evaluate the plan.
   - Check the soft criteria for approach, semantics, and assumptions.
   - Check the qubit limit. If retrying, also check the build time limit from previous attempts. 
4. Write the plan to `PLAN.md`.
5. Decide which variables users can tweak. Aim for two or three variables and choose sensible defaults for each.

## Build

1. Create, read, update, and delete Python files as needed.
2. When the agent thinks the implementation is done, compare it with `PLAN.md`.
   - If it matches, proceed to **Test**.
   - If it falls short, return to step 1 of **Build**.

## Test

1. Run `main.py`.
2. Evaluate the build result.
   - If it passes, continue to the semantic check.
   - If it fails, return to **Build** with the error.
3. Evaluate semantics by comparing the implementation with the problem statement. At this point, assume the plan already matches the problem statement.
   - If the implementation matches, proceed to **Final Go/No-Go**.
   - If it falls short, return to **Build** with the semantic issues found.

## Final Go/No-Go

1. Compare the problem statement with the original NLP.
   - If it passes, continue to the plan check.
   - If it fails, return to the NLP parsing step in **Init**.
2. Compare the plan with the problem statement.
   - If it passes, continue to the implementation check.
   - If it fails, return to **Plan**.
3. Compare the implementation with the plan.
   - If it passes, proceed to **GO**.
   - If it fails, return to **Build**.
