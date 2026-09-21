import sys
from pathlib import Path

# Allow `from app... import` regardless of the directory pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent))
