"""
Pytest configuration for monitoreo-agente.

Ensures the project root is on sys.path so that the 'agente' package is
importable regardless of the working directory pytest is invoked from.

Author: Daniel Perez
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
