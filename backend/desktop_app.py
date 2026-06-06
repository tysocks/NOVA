import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MAX_PORT = 8010


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


def wait_for_nova_health(port: int, timeout_s: float = 20.0) -> bool:
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


def write_active_port(project_root: Path, port: int) -> None:
    (project_root / ".nova_backend_port").write_text(str(port), encoding="utf-8")


def clear_active_port(project_root: Path) -> None:
    port_file = project_root / ".nova_backend_port"
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
    existing = find_existing_nova_port()
    if existing is not None:
        write_active_port(project_root, existing)
        return existing, None, None, ""

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
    if not wait_for_nova_health(free_port, timeout_s=25.0):
        extra = ""
        if server.poll() is not None:
            extra = f"\nBackend process exited early (code {server.returncode})."
        if blocked:
            extra += (
                f"\nPort {DEFAULT_PORT} is used by another application; "
                f"NOVA tried port {free_port} without success."
            )
        server.terminate()
        log_file.close()
        return (
            None,
            None,
            None,
            f"Backend failed to start.\nSee log: {log_path}{extra}",
        )

    write_active_port(project_root, free_port)
    return free_port, server, log_file, ""


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
    profile_root = project_root / ".nova_profile"
    profile_root.mkdir(parents=True, exist_ok=True)
    profile = QWebEngineProfile.defaultProfile()
    profile.setPersistentStoragePath(str(profile_root / "storage"))
    profile.setCachePath(str(profile_root / "cache"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
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
        web_settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        web_settings.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, False)
        view.setWindowTitle("NOVA")
        if icon_path.exists():
            view.setWindowIcon(QIcon(str(icon_path)))
        view.resize(1500, 920)
        view.setUrl(QUrl(f"http://{HOST}:{port}/?v={int(time.time())}"))
        view.show()
        splash.finish(view)

        exit_code = app.exec()
    finally:
        if server:
            server.terminate()
            try:
                server.wait(timeout=5)
            except Exception:
                server.kill()
            clear_active_port(project_root)
        if log_file:
            log_file.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
