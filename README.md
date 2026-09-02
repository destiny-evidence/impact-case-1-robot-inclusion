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
