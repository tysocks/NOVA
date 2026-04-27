import os
import socket
import subprocess
import sys
import time
import json
from urllib.request import urlopen
from urllib.error import URLError
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QPixmap


def wait_for_port(host: str, port: int, timeout_s: float = 20.0) -> bool:
    start = time.time()
    while time.time() - start < timeout_s:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


def wait_for_nova_health(timeout_s: float = 20.0) -> bool:
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            with urlopen("http://127.0.0.1:8000/health", timeout=0.8) as resp:
                if resp.status != 200:
                    time.sleep(0.2)
                    continue
                payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("ok") is True and payload.get("app") == "NOVA":
                    return True
        except (URLError, OSError, ValueError):
            pass
        time.sleep(0.2)
    return False


def main() -> None:
    backend_dir = Path(__file__).resolve().parent
    project_root = backend_dir.parent
    log_path = project_root / "nova_backend.log"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir)

    app = QApplication(sys.argv)
    splash = QSplashScreen(QPixmap(500, 240))
    splash.showMessage("Starting NOVA...", alignment=Qt.AlignCenter | Qt.AlignBottom, color=Qt.white)
    splash.show()
    app.processEvents()

    backend_was_running = wait_for_nova_health(timeout_s=0.8)
    server = None
    log_file = None
    if not backend_was_running:
        python_for_backend = Path(sys.executable).with_name("python.exe")
        if not python_for_backend.exists():
            python_for_backend = Path(sys.executable)
        log_file = open(log_path, "a", encoding="utf-8")
        log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Launching backend\n")
        log_file.flush()
        server = subprocess.Popen(
            [str(python_for_backend), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=str(backend_dir),
            env=env,
            stdout=log_file,
            stderr=log_file,
        )

    try:
        if not wait_for_nova_health(timeout_s=25.0):
            extra = ""
            if server and server.poll() is not None:
                extra = f"\nBackend process exited early (code {server.returncode})."
            QMessageBox.critical(
                None,
                "NOVA",
                "Backend failed to start on port 8000.\n"
                f"See log: {log_path}{extra}",
            )
            if server:
                server.terminate()
            sys.exit(1)

        view = QWebEngineView()
        view.setWindowTitle("NOVA")
        view.resize(1500, 920)
        # Cache-bust static UI so latest frontend changes always load.
        view.setUrl(QUrl(f"http://127.0.0.1:8000/?v={int(time.time())}"))
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
        if log_file:
            log_file.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
