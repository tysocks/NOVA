from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_user_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep library/settings/runtime files out of the real user profile and repo."""
    data = tmp_path / "userdata"
    data.mkdir()
    libs = data / "libraries"
    libs.mkdir()
    monkeypatch.setenv("NOVA_USER_DATA_DIR", str(data))

    settings = data / ".nova_user_settings.json"
    monkeypatch.setattr("app.services.user_settings.USER_SETTINGS_FILE", settings)

    mapping = {
        "units": libs / ".nova_unit_library.json",
        "configurations": libs / ".nova_config_library.json",
        "range_definitions": libs / ".nova_range_definition_library.json",
        "ingestion_rules": libs / ".nova_ingestion_library.json",
        "limits": libs / ".nova_limit_library.json",
    }
    monkeypatch.setattr("app.main.UNIT_LIBRARY_FILE", mapping["units"])
    monkeypatch.setattr("app.main.CONFIG_LIBRARY_FILE", mapping["configurations"])
    monkeypatch.setattr("app.main.RANGE_DEFINITION_LIBRARY_FILE", mapping["range_definitions"])
    monkeypatch.setattr("app.main.INGESTION_LIBRARY_FILE", mapping["ingestion_rules"])
    monkeypatch.setattr("app.main.LIMIT_LIBRARY_FILE", mapping["limits"])

    db_lib = data / ".nova_database_library.json"
    monkeypatch.setattr("app.main.DATABASE_LIBRARY_FILE", db_lib)
    monkeypatch.setattr("app.services.database_library.DATABASE_LIBRARY_FILE", db_lib)
    monkeypatch.setattr("app.main.APPEARANCE_FILE", data / ".nova_appearance.json")
    monkeypatch.setattr("app.main.SOURCES_WORKSPACE_FILE", data / ".nova_sources_workspace.json")

    sessions = data / ".nova_sessions"
    catalog = data / ".nova_catalog.duckdb"
    monkeypatch.setattr("app.engine.session_store.SESSIONS_ROOT", sessions)
    monkeypatch.setattr("app.engine.catalog_store.DEFAULT_CATALOG_DB_PATH", catalog)
    monkeypatch.setattr("app.engine.catalog_store.CATALOG_DB_PATH", catalog)
    monkeypatch.setattr("app.config.settings.parquet_root", str(data / ".nova_parquet"))

    return settings
