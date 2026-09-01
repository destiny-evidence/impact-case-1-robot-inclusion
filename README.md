
```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up/down/ps
```

## Edit a model file

```python
import joblib
import numpy as np

info = joblib.load('../impact-case-1/data/models/testing/inout/results/model/filtering/weights/model.sklearn')
info['classes_'] = np.array([0, 1])
info['threshold_'] = 0.02
joblib.dump(info, '.configs/models/high-recall-svm.sklearn')
```