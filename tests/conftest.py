import sys
from unittest.mock import MagicMock

if "vim" not in sys.modules:
    sys.modules["vim"] = MagicMock()
