"""Ensure repo root is on sys.path so `strands_adb` imports in tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
