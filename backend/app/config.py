from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"


class Settings(BaseSettings):
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "hfr_test_data"
    db_user: str = "pipeline"
    db_password: str = "test"
    db_sslmode: str = "disable"
    db_name_redscale: str | None = None
    db_name_bluescale: str | None = None
    redscale_host: str = "localhost"
    redscale_port: int = 5432
    redscale_user: str = "pipeline"
    redscale_password: str = "test"
    redscale_sslmode: str = "disable"
    bluescale_host: str = "localhost"
    bluescale_port: int = 5433
    bluescale_user: str = "analyst"
    bluescale_password: str = "test"
    bluescale_sslmode: str = "disable"
    default_limit: int = 5000

    model_config = SettingsConfigDict(env_prefix="NOVA_", env_file=str(ENV_PATH), extra="ignore")

    def dsn(self, db_name: str | None = None) -> str:
        target_db = db_name or self.db_name
        return (
            f"host={self.db_host} "
            f"port={self.db_port} "
            f"dbname={target_db} "
            f"user={self.db_user} "
            f"password={self.db_password} "
            f"sslmode={self.db_sslmode}"
        )


settings = Settings()
