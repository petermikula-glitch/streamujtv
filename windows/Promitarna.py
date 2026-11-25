"""Vstup Promítárny pro Windows (z něj PyInstaller sestaví Promitarna.exe)."""

from promitarna_win.app import main

if __name__ == "__main__":
    raise SystemExit(main())
