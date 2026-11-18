# Hack The Nest: Quantum Optimization Agent

Describe an optimization problem in plain English. The agent turns it into a quantum algorithm, runs it on the Quantum Rings simulator, checks the answer, and saves the result.

Steps the agent goes through: **Init → Plan → Build → Test → Final Go/No-Go → GO**.

## 1. What you need

- Python 3.11 or newer (tested with 3.12)
- Git
- A Quantum Rings account and license key: <https://www.quantumrings.com>
- A [Gemini API key](https://aistudio.google.com/apikey) or an [OpenRouter API key](https://openrouter.ai/settings/keys)

## 2. Get the code

```sh
git clone https://github.com/Integer-Conversion-Error/hack-the-nest.git
cd hack-the-nest
```

## 3. Create a virtual environment

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
```

## 4. Install the packages

```sh
pip install -r requirements.txt
pip install -r quantum-playground/requirements.txt
```

The first line installs `QuantumRingsLib`, `qiskit`, `matplotlib`, `google-genai` and the rest of the backend. The second adds what the dashboard needs.

## 5. Add your keys

Copy the example file:

```sh
cp .env.example .env
```

Open `.env` and fill in your Quantum Rings credentials and selected model provider:

```
QR_KEY=your-quantum-rings-key
QR_ACCOUNT=your-quantum-rings-account-email
GEMINI_API_KEY=your-gemini-key
```

Gemini remains the default. To use OpenRouter, set `MODEL_PROVIDER=openrouter`,
`OPENROUTER_API_KEY`, and `OPENROUTER_MODEL` instead. You can choose separate
models for interpretation, research, planning, building, and reviews. See the
[model provider guide](backend/MODEL_PROVIDERS.md) for configuration, shared
retry budgets, and the usage artifacts written to each workspace.

`.env` is ignored by git. Never commit it.

## 6. Run the program

Run everything from the repository root.

```sh
python -m backend.pipeline example-user-prompts/robotics-team-split.md
```

A full run takes about 5 to 25 minutes. It prints each step as it goes and ends with:

```
GO: <problem title>
Method: <method> on <n> qubits
Result: <value> <unit>
Handoff for the frontend: workspaces/<id>/handoff.json
```

Other prompts to try:

| Prompt file | Problem | Correct answer |
| --- | --- | --- |
| `example-user-prompts/robotics-team-split.md` | Split six students into two teams | Friction 3 |
| `example-user-prompts/food-truck-weekend.md` | Pick weekend events for a food truck | $1,480 |
| `example-user-prompts/ev-fleet-dispatch-optimization.md` | Schedule two electric vans | $15.78 |

To use your own problem, write it in a `.md` or `.txt` file and pass that file instead.

### If a run stops partway

The first line of output is `Workspace: <id>`. Resume with that id:

```sh
python -m backend.pipeline --workspace <id>
```

If Plan asks a question, answer it:

```sh
python -m backend.pipeline --workspace <id> --answer "your answer"
```

## 7. Look at the results

Each run gets its own folder in `workspaces/<id>/`:

| File | What it is |
| --- | --- |
| `PROBLEM.md` | The problem as the agent understood it |
| `PLAN.md` | The solver plan |
| `main.py` | The solver the agent wrote |
| `results.json` | The answer and the quantum measurements |
| `circuit.png` | Picture of the quantum circuit |
| `handoff.json` | Everything the frontend needs |

Rerun a finished solver with a different setting (the settings are listed under `controls` in `handoff.json`):

```sh
cd workspaces/<id>
python main.py --<control key> <value>
```

## 8. Run one step at a time (optional)

```sh
python -m backend.init <prompt-file>        # new workspace + problem statement
python -m backend.build <id>                # write the solver
python -m backend.build_review <id>         # compare the solver with the plan
python -m backend.testing <id>              # run and check the solver
python -m backend.final_check <id>          # final go / no-go
```

## 9. Run the dashboard (optional)

```sh
python quantum-playground/server.py
```

Open <http://127.0.0.1:8000>. See [quantum-playground/README.md](quantum-playground/README.md) for details.

## 10. Run the tests

```sh
python -m unittest backend/tests/test_dashboard_pipeline.py
```

Expected output ends with `OK`. Dashboard tests are described in [quantum-playground/docs/DEVELOPMENT.md](quantum-playground/docs/DEVELOPMENT.md).

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `No module named ...` | Activate the virtual environment (step 3) and install again (step 4) |
| `Set QR_KEY and QR_ACCOUNT` | Fill in `.env` (step 5) |
| `Configure GEMINI_API_KEY` | Fill in `.env` (step 5) |
| Gemini error 503 or "high demand" | Wait a minute and resume the workspace (step 6) |
| Run ends with `NO-GO` or `Stopped in ...` | Read the issues it prints, then resume the workspace or start a new run |

## Project layout

| Folder | Contents |
| --- | --- |
| `backend/` | The agent pipeline; all prompts are in `backend/prompts.md` |
| `example-user-prompts/` | Sample problems |
| `quantum-playground/` | The dashboard |
| `workspaces/` | One folder per run (ignored by git) |

## Credits
Made by: 
- Chenkai Mu
- Ehan Hassan
- Esad Kaya
- Maegen Hill
