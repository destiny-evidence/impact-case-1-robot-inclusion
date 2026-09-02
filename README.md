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

## Development notes

### Testing

```bash
# if needed, clear caches
pre-commit clear
# run checks on all files
pre-commit run --all-files
```

### Testing with local ODS

When running the open data system locally for testing and you need overrides, remember to explicitly set the override
file in `dc up`:

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up/down/ps
```

### Edit a model file

You can use any sklearn model from the `impact-case-1` repository. Make sure all relevant fields are actually set. In
case something is missing, you can edit the serialised model file like so:

```python
import joblib
import numpy as np

info = joblib.load('../impact-case-1/data/models/testing/inout/results/model/filtering/weights/model.sklearn')
info['classes_'] = np.array([0, 1])
info['threshold_'] = 0.02
joblib.dump(info, '.configs/models/high-recall-svm.sklearn')
```
