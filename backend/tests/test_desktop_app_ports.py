from unittest.mock import patch

from desktop_app import (
    fetch_nova_health,
    find_existing_nova_port,
    find_free_port,
    ports_used_by_other_apps,
)


def test_fetch_nova_health_accepts_nova_payload():
    class FakeResp:
        status = 200

        def read(self):
            return b'{"ok": true, "app": "NOVA"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("desktop_app.urlopen", return_value=FakeResp()):
        assert fetch_nova_health(8000) is True


def test_fetch_nova_health_rejects_foreign_app():
    class FakeResp:
        status = 404

        def read(self):
            return b'{"detail":"Not Found"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("desktop_app.urlopen", return_value=FakeResp()):
        assert fetch_nova_health(8000) is False


def test_find_existing_nova_port_scans_range():
    with patch("desktop_app.fetch_nova_health", side_effect=[False, True, False]):
        assert find_existing_nova_port() == 8001


def test_ports_used_by_other_apps():
    def fake_listening(_host: str, port: int) -> bool:
        return port == 8000

    with patch("desktop_app.port_is_listening", side_effect=fake_listening):
        with patch("desktop_app.fetch_nova_health", return_value=False):
            assert ports_used_by_other_apps() == [8000]


def test_find_free_port_skips_listening_ports():
    def fake_listening(_host: str, port: int) -> bool:
        return port == 8000

    with patch("desktop_app.port_is_listening", side_effect=fake_listening):
        assert find_free_port() == 8001
