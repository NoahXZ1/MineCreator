"""Frozen desktop entry point."""
import ctypes
import sys
from scripts.run_gui import main

if __name__ == '__main__':
    try:
        main()
    except Exception:
        if sys.stderr is None:
            ctypes.windll.user32.MessageBoxW(None,
                'MineCreator could not start. Close an existing instance, then try again. Your saved data is kept in %LOCALAPPDATA%\\MineCreator.',
                'MineCreator',0x10)
        else:
            raise
