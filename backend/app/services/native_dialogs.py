"""Native OS dialogs for the desktop app.

Uvicorn runs sync FastAPI handlers in a thread pool. Tk and Windows COM
folder dialogs need their own process, so pickers are launched as a child.
The chosen path is written to a temp file so CREATE_NO_WINDOW cannot swallow it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class _FolderPickCancelled(Exception):
    """User dismissed the folder picker."""


class _FolderPickUnavailable(Exception):
    """Raised when the preferred native picker cannot be created."""


def pick_folder(title: str = "Select folder") -> str | None:
    if os.environ.get("NOVA_FOLDER_PICKER_CHILD") == "1":
        return _pick_folder_in_process(title)

    out_path = Path(tempfile.gettempdir()) / f"nova_folder_pick_{os.getpid()}_{time.time_ns()}.txt"
    env = os.environ.copy()
    env["NOVA_FOLDER_PICKER_CHILD"] = "1"
    env["NOVA_FOLDER_PICKER_OUT"] = str(out_path)
    kwargs: dict = {
        "args": [sys.executable, "-u", str(Path(__file__).resolve()), title],
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
        "timeout": 600,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(**kwargs)
    except Exception:
        return _read_picker_out(out_path)
    chosen = _read_picker_out(out_path)
    if chosen:
        return chosen
    lines = (completed.stdout or "").strip().splitlines()
    fallback = lines[-1].strip() if lines else ""
    if not fallback:
        return None
    return str(Path(fallback).expanduser().resolve())


def _read_picker_out(out_path: Path) -> str | None:
    text = ""
    try:
        if out_path.exists():
            text = out_path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
    if not text:
        return None
    return str(Path(text).expanduser().resolve())


def _write_picker_out(selected: str | None) -> None:
    out = str(os.environ.get("NOVA_FOLDER_PICKER_OUT") or "").strip()
    if not selected or not out:
        return
    Path(out).write_text(selected, encoding="utf-8")


def _pick_folder_in_process(title: str) -> str | None:
    if sys.platform == "win32":
        try:
            return _pick_folder_win32(title)
        except _FolderPickCancelled:
            return None
        except _FolderPickUnavailable:
            return _pick_folder_tk(title)
        except Exception:
            return _pick_folder_tk(title)
    return _pick_folder_tk(title)


def _pick_folder_tk(title: str) -> str | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        root.lift()
        root.focus_force()
        root.update()
        chosen = filedialog.askdirectory(parent=root, title=title, mustexist=True)
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return str(Path(chosen).resolve()) if chosen else None


def _pick_folder_win32(title: str) -> str | None:
    """Modern Explorer folder picker (IFileOpenDialog + FOS_PICKFOLDERS)."""
    import ctypes
    import uuid
    from ctypes import HRESULT, POINTER, byref, c_void_p, c_wchar_p
    from ctypes.wintypes import DWORD, HWND, LPCWSTR, LPVOID

    CANCELLED = 0x800704C7
    SIGDN_FILESYSPATH = ctypes.c_int(0x80058000).value

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", DWORD),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        def __init__(self, value: str):
            super().__init__()
            parsed = uuid.UUID(value)
            self.Data1 = parsed.time_low
            self.Data2 = parsed.time_mid
            self.Data3 = parsed.time_hi_version
            for index, byte in enumerate(parsed.bytes[8:]):
                self.Data4[index] = byte

    class Vtbl(ctypes.Structure):
        _fields_ = [("fns", c_void_p * 32)]

    CLSID_FileOpenDialog = GUID("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
    IID_IFileOpenDialog = GUID("{D57C7288-D4AD-4768-BE02-9D969532D960}")
    CLSCTX_INPROC_SERVER = 1
    FOS_PICKFOLDERS = 0x20
    FOS_FORCEFILESYSTEM = 0x40

    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitialize(None)
    ole32.CoCreateInstance.argtypes = [
        POINTER(GUID),
        LPVOID,
        DWORD,
        POINTER(GUID),
        POINTER(c_void_p),
    ]
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CoTaskMemFree.argtypes = [c_void_p]
    ole32.CoTaskMemFree.restype = None

    dialog_ptr = c_void_p()
    created = ole32.CoCreateInstance(
        byref(CLSID_FileOpenDialog),
        None,
        CLSCTX_INPROC_SERVER,
        byref(IID_IFileOpenDialog),
        byref(dialog_ptr),
    )
    if created != 0 or not dialog_ptr.value:
        raise _FolderPickUnavailable("CoCreateInstance failed")

    vtbl = ctypes.cast(dialog_ptr, POINTER(POINTER(Vtbl))).contents.contents
    this = dialog_ptr
    fn = vtbl.fns

    def com(index, restype, *argtypes):
        return ctypes.WINFUNCTYPE(restype, *argtypes)(fn[index])

    release = com(2, ctypes.c_ulong, LPVOID)
    show = com(3, HRESULT, LPVOID, HWND)
    set_options = com(9, HRESULT, LPVOID, DWORD)
    get_options = com(10, HRESULT, LPVOID, POINTER(DWORD))
    set_title = com(17, HRESULT, LPVOID, LPCWSTR)
    get_result = com(21, HRESULT, LPVOID, POINTER(c_void_p))

    options = DWORD(0)
    if get_options(this, byref(options)) == 0:
        set_options(this, options.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM)
    else:
        set_options(this, FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM)
    set_title(this, title)

    shown = int(show(this, None)) & 0xFFFFFFFF
    if shown == CANCELLED:
        release(this)
        raise _FolderPickCancelled()
    if shown != 0:
        release(this)
        raise _FolderPickUnavailable(f"Show failed: 0x{shown:08X}")

    item_ptr = c_void_p()
    if get_result(this, byref(item_ptr)) != 0 or not item_ptr.value:
        release(this)
        return None

    item_vtbl = ctypes.cast(item_ptr, POINTER(POINTER(Vtbl))).contents.contents
    get_display_name = ctypes.WINFUNCTYPE(HRESULT, LPVOID, ctypes.c_int, POINTER(c_wchar_p))(item_vtbl.fns[5])
    item_release = ctypes.WINFUNCTYPE(ctypes.c_ulong, LPVOID)(item_vtbl.fns[2])
    display = c_wchar_p()
    ok = get_display_name(item_ptr, SIGDN_FILESYSPATH, byref(display))
    path = display.value if ok == 0 and display.value else None
    if display:
        ole32.CoTaskMemFree(display)
    item_release(item_ptr)
    release(this)
    return str(Path(path).resolve()) if path else None


if __name__ == "__main__":
    selected = _pick_folder_in_process(sys.argv[1] if len(sys.argv) > 1 else "Select folder")
    _write_picker_out(selected)
    if selected:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        sys.stdout.write(selected)
        sys.stdout.flush()
        sys.exit(0)
    sys.exit(1)
