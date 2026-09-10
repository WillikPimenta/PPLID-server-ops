import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from web_background import stop_server

stop_server()
