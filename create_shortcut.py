#!/usr/bin/env python3
"""
Creates a desktop shortcut for Maps Scraper.
Run once after cloning: py create_shortcut.py
"""

import os
import sys
import winreg
from pathlib import Path


def find_pythonw() -> str:
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    return str(pythonw) if pythonw.exists() else str(python)


def create_shortcut():
    try:
        import comtypes.client  # noqa
    except ImportError:
        pass

    try:
        from win32com.shell import shell  # noqa
    except ImportError:
        pass

    # Use WScript.Shell via ctypes/subprocess-free approach
    import subprocess
    pythonw   = find_pythonw()
    proj_dir  = Path(__file__).parent.resolve()
    gui_path  = proj_dir / "gui.py"
    desktop   = Path(os.environ.get("USERPROFILE", "~")).expanduser() / "Desktop"
    lnk_path  = desktop / "Maps Scraper.lnk"

    ps = f"""
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut('{lnk_path}')
$sc.TargetPath = '{pythonw}'
$sc.Arguments = '"{gui_path}"'
$sc.WorkingDirectory = '{proj_dir}'
$sc.Description = 'Google Maps Business Scraper'
$sc.IconLocation = '{pythonw},0'
$sc.Save()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"Shortcut created: {lnk_path}")
    else:
        print(f"Failed: {result.stderr.strip()}")
        sys.exit(1)


if __name__ == "__main__":
    if sys.platform != "win32":
        print("This script is Windows-only.")
        sys.exit(1)
    create_shortcut()
