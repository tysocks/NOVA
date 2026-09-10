import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from PySide6.QtCore import QUrl, Qt, QEvent, QTimer
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen, QMainWindow

from app.paths import backend_port_file, ensure_user_data_dir, profile_root as nova_profile_root

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MAX_PORT = 8010
BACKEND_HEALTH_TIMEOUT_S = 180.0


def port_is_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def fetch_nova_health(port: int) -> bool:
    try:
        with urlopen(f"http://{HOST}:{port}/health", timeout=0.8) as resp:
            if resp.status != 200:
                return False
            payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("ok") is True and payload.get("app") == "NOVA"
    except (URLError, OSError, ValueError):
        return False


def wait_for_nova_health(port: int, timeout_s: float = BACKEND_HEALTH_TIMEOUT_S) -> bool:
    start = time.time()
    while time.time() - start < timeout_s:
        if fetch_nova_health(port):
            return True
        time.sleep(0.2)
    return False


def find_existing_nova_port() -> int | None:
    for port in range(DEFAULT_PORT, MAX_PORT + 1):
        if fetch_nova_health(port):
            return port
    return None


def pids_listening_on_port(port: int) -> list[int]:
    """Return PIDs with a listening socket on host port."""
    if sys.platform != "win32":
        return []
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return []
    found: list[int] = []
    marker = re.compile(rf":{port}\s")
    for line in (completed.stdout or "").splitlines():
        if "LISTENING" not in line.upper() or not marker.search(line):
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 4:
            found.append(pid)
    return list(dict.fromkeys(found))


def kill_pid_tree(pid: int) -> None:
    if pid <= 4:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def stop_backend_process(server: subprocess.Popen | None) -> None:
    if server is None:
        return
    pid = getattr(server, "pid", None)
    if pid:
        kill_pid_tree(pid)
    else:
        try:
            server.terminate()
        except Exception:
            pass
    try:
        server.wait(timeout=5)
    except Exception:
        try:
            server.kill()
        except Exception:
            pass


def stop_existing_nova_backends() -> None:
    """Kill orphaned NOVA uvicorn processes so the next launch loads current code."""
    for port in range(DEFAULT_PORT, MAX_PORT + 1):
        if not fetch_nova_health(port):
            continue
        for pid in pids_listening_on_port(port):
            kill_pid_tree(pid)
    deadline = time.time() + 8
    while time.time() < deadline:
        if find_existing_nova_port() is None:
            return
        time.sleep(0.15)


def ports_used_by_other_apps() -> list[int]:
    blocked: list[int] = []
    for port in range(DEFAULT_PORT, MAX_PORT + 1):
        if port_is_listening(HOST, port) and not fetch_nova_health(port):
            blocked.append(port)
    return blocked


def find_free_port() -> int | None:
    for port in range(DEFAULT_PORT, MAX_PORT + 1):
        if not port_is_listening(HOST, port):
            return port
    return None


def write_active_port(_project_root: Path | None = None, port: int = 0) -> None:
    path = backend_port_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(port), encoding="utf-8")


def clear_active_port(_project_root: Path | None = None) -> None:
    port_file = backend_port_file()
    if port_file.exists():
        port_file.unlink()


def start_backend_process(
    *,
    backend_dir: Path,
    env: dict[str, str],
    log_file,
    port: int,
) -> subprocess.Popen:
    python_for_backend = Path(sys.executable).with_name("python.exe")
    if not python_for_backend.exists():
        python_for_backend = Path(sys.executable)
    log_file.write(
        f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Launching backend on {HOST}:{port}\n"
    )
    log_file.flush()
    return subprocess.Popen(
        [
            str(python_for_backend),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            HOST,
            "--port",
            str(port),
        ],
        cwd=str(backend_dir),
        env=env,
        stdout=log_file,
        stderr=log_file,
    )


def resolve_backend_port(
    *,
    backend_dir: Path,
    project_root: Path,
    env: dict[str, str],
    log_path: Path,
) -> tuple[int | None, subprocess.Popen | None, object | None, str]:
    """Return (port, server_process, log_file_handle, error_message)."""
    stop_existing_nova_backends()

    blocked = ports_used_by_other_apps()
    free_port = find_free_port()
    if free_port is None:
        blocked_text = ", ".join(str(p) for p in blocked) or "unknown"
        return (
            None,
            None,
            None,
            "All NOVA backend ports are in use (8000–8010).\n"
            f"Ports held by other apps: {blocked_text}\n"
            "Close those applications or free a port, then try again.",
        )

    log_file = open(log_path, "a", encoding="utf-8")
    if blocked:
        log_file.write(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"Port {DEFAULT_PORT} busy (other app); using {free_port}. "
            f"Blocked: {', '.join(str(p) for p in blocked)}\n"
        )
        log_file.flush()

    server = start_backend_process(
        backend_dir=backend_dir,
        env=env,
        log_file=log_file,
        port=free_port,
    )
    if not wait_for_nova_health(free_port, timeout_s=BACKEND_HEALTH_TIMEOUT_S):
        extra = ""
        if server.poll() is not None:
            extra = f"\nBackend process exited early (code {server.returncode})."
        else:
            extra = (
                f"\nBackend did not become healthy within {int(BACKEND_HEALTH_TIMEOUT_S)} seconds."
            )
        if blocked:
            extra += (
                f"\nPort {DEFAULT_PORT} is used by another application; "
                f"NOVA tried port {free_port} without success."
            )
        stop_backend_process(server)
        log_file.close()
        return (
            None,
            None,
            None,
            f"Backend failed to start.\nSee log: {log_path}{extra}",
        )

    write_active_port(project_root, free_port)
    return free_port, server, log_file, ""


class NovaMainWindow(QMainWindow):
    """Main window that forces a layout pass after maximize/restore.

    Qt WebEngine often keeps a stale 0×0/wrong viewport after maximize until the
    next manual resize; nudge the page so CSS/Plotly recover without user action.
    """

    def __init__(self, view: QWebEngineView):
        super().__init__()
        self._view = view
        self.setCentralWidget(view)
        self.setWindowTitle(view.windowTitle() or "NOVA")
        if not view.windowIcon().isNull():
            self.setWindowIcon(view.windowIcon())

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event is None or event.type() != QEvent.Type.WindowStateChange:
            return
        # Defer until after Qt finishes applying the new geometry.
        QTimer.singleShot(0, self._nudge_web_layout)
        QTimer.singleShot(80, self._nudge_web_layout)
        QTimer.singleShot(250, self._nudge_web_layout)

    def _nudge_web_layout(self) -> None:
        try:
            page = self._view.page()
            if page is None:
                return
            page.runJavaScript(
                "try{"
                "window.dispatchEvent(new Event('resize'));"
                "if(typeof window.novaForceLayoutRefresh==='function') window.novaForceLayoutRefresh();"
                "}catch(e){}"
            )
        except Exception:
            pass


def main() -> None:
    backend_dir = Path(__file__).resolve().parent
    project_root = backend_dir.parent
    log_path = project_root / "nova_backend.log"
    icon_path = project_root / "assets" / "NOVA_ICON.png"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir)

    app = QApplication(sys.argv)
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    ensure_user_data_dir()
    profile_root = nova_profile_root()
    profile_root.mkdir(parents=True, exist_ok=True)
    profile = QWebEngineProfile.defaultProfile()
    profile.setPersistentStoragePath(str(profile_root / "storage"))
    profile.setCachePath(str(profile_root / "cache"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
    try:
        cache_type = getattr(getattr(QWebEngineProfile, "HttpCacheType", None), "NoCache", None)
        if cache_type is not None:
            profile.setHttpCacheType(cache_type)
        profile.setHttpCacheMaximumSize(0)
        profile.clearHttpCache()
    except Exception:
        pass
    splash_pix = QPixmap(520, 520)
    if icon_path.exists():
        splash_pix.fill(Qt.black)
        icon_pix = QPixmap(str(icon_path))
        if not icon_pix.isNull():
            icon_pix = icon_pix.scaled(390, 390, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter = QPainter(splash_pix)
            painter.drawPixmap((splash_pix.width() - icon_pix.width()) // 2, 34, icon_pix)
            painter.end()
    else:
        splash_pix.fill(Qt.black)
    splash = QSplashScreen(splash_pix)
    if icon_path.exists():
        splash.setWindowIcon(QIcon(str(icon_path)))
    splash.showMessage("Starting NOVA...", alignment=Qt.AlignCenter | Qt.AlignBottom, color=Qt.white)
    splash.show()
    app.processEvents()

    port, server, log_file, error = resolve_backend_port(
        backend_dir=backend_dir,
        project_root=project_root,
        env=env,
        log_path=log_path,
    )
    if port is None:
        QMessageBox.critical(None, "NOVA", error)
        sys.exit(1)

    try:
        view = QWebEngineView()
        web_settings = view.settings()
        web_settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        try:
            web_settings.setAttribute(QWebEngineSettings.JavascriptCanAccessClipboard, True)
            if hasattr(QWebEngineSettings, "JavascriptCanPaste"):
                web_settings.setAttribute(QWebEngineSettings.JavascriptCanPaste, True)
        except Exception:
            pass
        web_settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        web_settings.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, False)
        try:
            page = view.page()
            if page is not None and hasattr(page, "featurePermissionRequested"):
                def _grant_clipboard(url, feature):
                    clipboard_features = []
                    for name in ("ClipboardReadWrite", "ClipboardUserGesture"):
                        value = getattr(QWebEnginePage.Feature, name, None)
                        if value is not None:
                            clipboard_features.append(value)
                    if feature in clipboard_features:
                        granted = getattr(
                            QWebEnginePage.PermissionPolicy,
                            "PermissionGrantedByUser",
                            getattr(QWebEnginePage, "PermissionGrantedByUser", None),
                        )
                        if granted is not None:
                            page.setFeaturePermission(url, feature, granted)
                page.featurePermissionRequested.connect(_grant_clipboard)
        except Exception:
            pass
        view.setWindowTitle("NOVA")
        if icon_path.exists():
            view.setWindowIcon(QIcon(str(icon_path)))

        window = NovaMainWindow(view)
        window.resize(1500, 920)
        view.setUrl(QUrl(f"http://{HOST}:{port}/?v={int(time.time())}"))
        window.show()
        splash.finish(window)

        exit_code = app.exec()
    finally:
        stop_backend_process(server)
        clear_active_port(project_root)
        if log_file:
            log_file.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
