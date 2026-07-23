"""strands-adb — @tool decorated Android control for Strands agents."""
from strands_adb.adb_tool import adb
from strands_adb.recorder import recorder

__version__ = "0.19.4"
__all__ = ["adb", "recorder"]
