"""Copy a PNG onto the OS clipboard (desktop share path)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def set_clipboard_png(png_bytes: bytes) -> None:
    if not png_bytes:
        raise ValueError("PNG payload is empty")
    if sys.platform == "win32":
        _set_clipboard_png_windows(png_bytes)
        return
    raise RuntimeError("Clipboard image copy is only available in the Windows desktop app.")


def _set_clipboard_png_windows(png_bytes: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(png_bytes)
        temp_path = Path(handle.name)
    try:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type -AssemblyName System.Drawing; "
            f"$img = [System.Drawing.Image]::FromFile({json.dumps(str(temp_path))}); "
            "[System.Windows.Forms.Clipboard]::SetImage($img); "
            "$img.Dispose()"
        )
        completed = subprocess.run(
            ["powershell", "-STA", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "clipboard command failed").strip()
            raise RuntimeError(detail)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
