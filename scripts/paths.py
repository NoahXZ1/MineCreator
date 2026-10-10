"""Separate writable user data from bundled application assets."""
import os
from pathlib import Path
import sys

ASSET_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
ROOT = (Path(os.environ['MINECREATOR_HOME']).expanduser().resolve() if os.environ.get('MINECREATOR_HOME') else
        Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'MineCreator' if getattr(sys, 'frozen', False) else ASSET_ROOT)
