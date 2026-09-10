"""Keep production imports deterministic during whole-suite collection."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The SDK fixture file ``tests/dnx64/backend.py`` can be imported by third-party
# collection logic under the short name ``backend``. Remove only that non-package
# collision so imports resolve to the real production package.
loaded_backend = sys.modules.get("backend")
if loaded_backend is not None and not hasattr(loaded_backend, "__path__"):
    sys.modules.pop("backend", None)
