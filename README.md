# Inclusion robot for DESTinY's climate and health map (impact case 1)

This repository hosts the code to classify the relevance of a reference using the models developed and evaluated
in [the project-specific repository](https://github.com/destiny-evidence/impact-case-1).

Currently, we are following a multi-step classification process to determine inclusion labels:

First, we apply a simple "cheap" model on _all_ references that match
our [climate and health query](https://github.com/destiny-evidence/impact-case-1/blob/main/ic1/query/revisions/query_20260408.py).
This model is tuned to have a very high recall (>95%). Although it has a low precision, it reduces the candidate set by
more than a half.

Secondly, we apply three LLM prompts to step-by-step improve the precision. The first _high-recall_ prompt is very
inclusive for identifying references on (climate AND (health OR mitigation actions OR adaptation actions)). Remaining
references are then passed to a second _balanced prompt_. Remaining references are finally passed to the third
_high-precision_ prompt to identify references on ((climate OR mitigation OR adaptation) AND health).

The LLM prompts can be configured to run in a majority vote mode. In that case, each prompt will be repeated until a
majority is reached. If you fixed the random seed and temperature, the seed will be offset on each iteration.

## Deployment notes

Configuration is controlled by `pydantic_settings` and is partially shared across all robots and partially specific to
each robot. These files are loaded by default in this order: `.env`, `.env.secret.shared`, `.env.secret`. You can
additionally add overrides by setting the respective environment variables. The designed use would be to have four
files: one shared and one per robot that is loaded into the environment variables before starting:

```text
# See .env.example.shared
.env.secret.shared

# Set individual robot IDs, etc; see .env.example.robot
.env.secret.robot-query
.env.secret.robot-prefilter
.env.secret.robot-llm
```

### Running a robot

First, [register the robots](https://destiny-evidence.github.io/destiny-repository/procedures/robot-registration.html).

```bash
# Set up shared config
cp .configs/.env.example.shared .env.secret.shared

# Place the secrets and IDs in the correct config files
cp .configs/.env.example.robot .env.secret.robot-query
cp .configs/.env.example.robot .env.secret.robot-prefilter
cp .configs/.env.example.robot .env.secret.robot-llm

# Run the robots
uv run --env-file .env.secret.shared --env-file .env.secret.robot-query robot query
uv run --env-file .env.secret.shared --env-file .env.secret.robot-prefilter robot prefilter
uv run --env-file .env.secret.shared --env-file .env.secret.robot-llm robot llm
```

### Prefilter model

The serialised sklearn model (`.configs/models/high-recall-svm.sklearn`) is not committed. The deploy workflow
downloads it from Azure blob storage into the build context before `docker build`, so it is baked into the image at
the path given by `MODEL_PREFILTER`. This needs:

- the `model_blob_url` Terraform variable, holding the full blob URL
  (`https://<account>.blob.core.windows.net/<container>/<blob>`). Terraform publishes it as the `MODEL_BLOB_URL`
  GitHub environment variable alongside the other `vars.*` the workflow uses — see `infra/github.tf`;
- the `Storage Blob Data Reader` role on that container (or account) for the GitHub Actions service principal, which
  authenticates via OIDC. The storage account is outside this stack, so this role is granted manually.

For local runs, put the file at `.configs/models/high-recall-svm.sklearn` yourself, or point `MODEL_PREFILTER`
elsewhere.

## Observability

With `OTEL_ENABLED=true`, each robot exports OpenTelemetry traces to Honeycomb over OTLP/HTTP. The ingest key and
endpoint come from `OTEL_CONFIG`, a JSON blob (see `.configs/.env.example.shared`); Terraform assembles it from the
`honeycomb_api_key` and `honeycomb_trace_endpoint` variables and injects it as a Container App secret.

Each robot reports to its own Honeycomb dataset, named `destiny-<task>-robot-<env>` — so
`destiny-query-robot-staging`, `destiny-prefilter-robot-staging` and `destiny-llm-robot-staging`.

One iteration of a robot's loop is one trace, rooted at a `robot.loop` span:

```text
robot.loop                       robot.task, app.batch.found, app.batch.id, app.reference.count
├─ POST /robot-enhancement-batches/          poll for work
├─ GET  <blob reference_storage_url>         fetch the batch's references
├─ …robot-specific work…
├─ PUT  <blob result_storage_url>            upload enhancements
└─ POST /robot-enhancement-batches/{id}/results/

prefilter: ─ prefilter.predict   one per sub-batch; app.batch.index, app.prefilter.included
llm:       ─ llm.prompt_round    one per prompt; app.llm.label, app.llm.included
             └─ litellm_request  one per completion (so one per majority vote), with gen_ai.* token counts
```

The HTTP spans come from httpx auto-instrumentation, which covers the repository client, the blob client and LiteLLM's
provider calls. LLM spans come from LiteLLM's own `otel` callback, which reuses the tracer provider set up in
`app/util/telemetry.py`. Idle iterations still emit a `robot.loop` span, carrying `app.batch.found=false`.

LiteLLM prompt span attributes (abstracts + prompts) are suppressed unless `OTEL_CAPTURE_LLM_CONTENT=true`; token counts, cost and model are reported either way.

## Development notes

### Build / test

```bash
# if needed, clear caches
pre-commit clear
# run checks on all files
pre-commit run --all-files
```

### Testing with local ODS

When running the open data system locally for integration testing you need overrides, remember to explicitly set the
override file in `dc up`:

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up/down/ps
```

### Edit a model file

You can use any sklearn model from the `impact-case-1` repository. Make sure all relevant fields are actually set. In
case something is missing, you can edit the serialised model file like so:

```python
import joblib
import numpy as np

info = joblib.load("../impact-case-1/data/models/testing/inout/results/model/filtering/weights/model.sklearn")
info["classes_"] = np.array([0, 1])
info["threshold_"] = 0.02
info.pop("pipeline")  # drop pipeline, otherwise import errors
joblib.dump(info, "../impact-case-1-robot-inclusion/.configs/models/high-recall-svm.sklearn")
```
