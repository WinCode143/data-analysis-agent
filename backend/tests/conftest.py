import os
import sys
import tempfile
from pathlib import Path

# The test workspace is isolated BEFORE importing the app.
os.environ["INSIGHT_WORKSPACE_DIR"] = tempfile.mkdtemp(prefix="insight_test_")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
