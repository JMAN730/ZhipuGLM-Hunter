"""Make the project root importable so tests can `import scanner_engine`
and `from scanners.base import ...` regardless of the working directory."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
