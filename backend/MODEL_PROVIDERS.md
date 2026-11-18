# Pipeline model providers

The backend supports Gemini and OpenRouter through the same message, tool,
schema, citation, and usage interface. Provider-specific SDK types stay in the
adapters. The API keys are server configuration; they are never sent to the
browser or recorded in model usage artifacts.

Set configuration in the repository root's ignored `.env`, then restart the
dashboard. The command-line stage entrypoints also load this file.

## Choose a provider

Gemini remains the default:

```dotenv
MODEL_PROVIDER=gemini
GEMINI_API_KEY=your-key
# Optional: GEMINI_MODEL=your-model-id
```

`GOOGLE_API_KEY` is also accepted. Without a global model override, the existing
Gemini default for each stage is retained. Install the repository requirements
to use Gemini.

For OpenRouter:

```dotenv
MODEL_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=vendor/model-id
```

Replace `vendor/model-id` with an actual OpenRouter model ID. Choose models and
endpoints that support structured outputs and function calling. The adapter
uses strict JSON schemas and `provider.require_parameters=true` so required
parameters cannot silently be ignored. Research uses the server-executed
`openrouter:web_search` tool and keeps returned URL citations. Research without
citations is recorded as unavailable.

See OpenRouter's [structured output documentation](https://openrouter.ai/docs/guides/features/structured-outputs)
and [web search documentation](https://openrouter.ai/docs/guides/features/server-tools/web-search)
for current endpoint capabilities.

## Select models per stage

Each model request resolves its own stage, including requests made during a
repair. For example:

```dotenv
MODEL_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=vendor/general-model
OPENROUTER_INTERPRET_MODEL=vendor/fast-model
OPENROUTER_RESEARCH_MODEL=vendor/research-model
OPENROUTER_PLAN_MODEL=vendor/planning-model
OPENROUTER_BUILD_MODEL=vendor/coding-model
OPENROUTER_REVIEW_MODEL=vendor/review-model
```

These model IDs are placeholders. All requests use the selected provider;
switching providers does not send requests to the previous provider's models.
The corresponding `GEMINI_..._MODEL` settings work with `MODEL_PROVIDER=gemini`.

| Stage | Model setting suffix |
| --- | --- |
| Interpret the prompt | `INTERPRET_MODEL` |
| Grounded research | `RESEARCH_MODEL` |
| Draft or revise the plan | `PLAN_MODEL` |
| Review the plan | `PLAN_REVIEW_MODEL` |
| Build or repair solver files | `BUILD_MODEL` |
| Review implementation against the plan | `BUILD_REVIEW_MODEL` |
| Review solver semantics | `SEMANTIC_REVIEW_MODEL` |
| Final problem and plan checks | `FINAL_REVIEW_MODEL` |

Resolution order is an explicit Python `model=` argument, the selected
provider's stage setting, its `REVIEW_MODEL` for review stages, its global
`MODEL`, then the existing Gemini stage default. OpenRouter requires a configured
model rather than substituting a Gemini model ID. A stage-specific review model
overrides the shared review setting. The final implementation check uses the
build-review model.

## Shared request and retry budgets

One pipeline invocation shares a budget across interpretation, planning,
building, testing reviews, final checks, and nested repair loops. Calling a
stage on its own creates a budget for that invocation. Temporary transport
failures consume this shared retry allowance; they do not spend a semantic
plan/build repair attempt. The provider adapters perform no hidden retries.

| Setting | Default | Meaning |
| --- | --- | --- |
| `MODEL_MAX_REQUESTS` | `100` | Maximum outbound model attempts, including failed attempts |
| `MODEL_MAX_RETRIES` | `8` | Additional attempts after temporary failures, shared across stages |
| `MODEL_TIMEOUT_SECONDS` | `1800` | Elapsed model deadline shared by the invocation |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | `120` | Maximum request timeout, also bounded by the remaining invocation time |
| `MODEL_MAX_OUTPUT_TOKENS` | `8192` | Output token cap sent with each model request |
| `MODEL_MAX_TOKENS` | unset | Optional cap based on cumulative reported token usage |
| `MODEL_MAX_COST` | unset | Optional cap based on cumulative reported cost in USD |

Requests stop when a budget is exhausted. Retry delays respect the remaining
time, and permanent failures such as invalid credentials are not retried.
The elapsed deadline is checked around model requests; solver workers retain
their separate execution timeouts.
Token and cost limits depend on provider usage reporting. Unknown usage remains
unknown; it is not treated as a confirmed zero-cost response. These limits do
not replace the provider account's spending controls.

## Usage artifacts

Each workspace records model attempts in `model_usage.jsonl` and a summary of
the latest invocation in `model_usage.json`. The trace includes the stage, configured provider and model,
actual model/provider when returned, request ID, duration, outcome, and reported
tokens and cost. Completion tokens include reasoning tokens; the reasoning
count is also available separately and must not be added again.

Failed attempts are recorded before an exception leaves the pipeline. Reported
usage from a billed but unusable completion also counts toward the budget. A later
invocation appends new attempts rather than deleting the prior trace. Prompts,
generated code, tool arguments/results, private reasoning, authorization
headers, and API keys are excluded from these artifacts.

The default verification commands use mocked adapters and perform no paid model
requests:

```sh
python3 -m unittest discover -s backend/tests -v
```
