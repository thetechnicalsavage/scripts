# v1.0 2026-10-10  Brief 12 pytest setup: puts code/ and code/py on sys.path so tests import the frozen modules.
#                  v1.0: first version.
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
for p in (CODE, CODE / "py", CODE / "data"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
