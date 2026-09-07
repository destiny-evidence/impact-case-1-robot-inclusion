"""
Test-wide environment setup.

`app.classifiers.llm` calls `get_settings()` at module scope, so importing anything
under `app` requires the two settings that have no default. Set them here, before any
test module imports `app`.
"""

import os
from uuid import uuid4

os.environ.setdefault("ROBOT_ID", str(uuid4()))
os.environ.setdefault("ROBOT_SECRET", "test-secret")
# Always disable telemetry for tests
os.environ["OTEL_ENABLED"] = "false"
