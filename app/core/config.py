from pathlib import Path

from dotenv import load_dotenv
from pydantic import SecretStr
from pydantic_settings import BaseSettings

load_dotenv()


class Config(BaseSettings):
    """Application settings and environment configuration manager.

    Loads configuration variables from environment files or environment variables,
    providing default fallback values and sensitive credential handling via Pydantic's
    SecretStr type.
    """

    app_name: str = "inwebstigator"
    automatic_scans: bool = True

    user_data_dir: Path = Path.home() / "AppData" / "Local" / app_name
    user_data_dir.mkdir(parents=True, exist_ok=True)

    db_name: str = f"{app_name}.db"
    db_path: Path = user_data_dir / db_name

    webview_storage_dir: Path = user_data_dir / "webview"

    email: str = ""
    email_password: SecretStr = SecretStr("")
    smtp_host: str = ""
    smtp_port: int = 465

    web_crawler_default_max_pages: int = 50_000
    web_crawler_default_delay: float = 0.5
    web_crawler_max_delay: float = 3
    web_crawler_default_concurrent: int = 5
    web_crawler_min_concurrent: int = 1
    web_crawler_batch_402_threshold_seconds: int = 50
    web_crawler_max_failed_attempts_at_min_speed: int = 3

    fetch_site_retry_max_attempts: int = 5
    fetch_site_retry_min_wait_seconds: int = 2
    fetch_site_retry_max_wait_seconds: int = 15
    fetch_site_retry_multiplier: int = 1

    text_similarity_threshold: float = 0.6  # TODO may have to drop to 0

    email_retry_max_attempts: int = 3
    email_retry_min_wait_seconds: int = 2
    email_retry_max_wait_seconds: int = 15
    email_retry_multiplier: int = 1

    scheduler_minimum_days_between_scans: float = 0.5
    scheduler_default_days_between_scans: float = 1
    scheduler_default_days_between_health_checks: float = 7

    @property
    def db_url(self) -> str:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def test_db_url(self) -> str:
        return "sqlite:///:memory:"


config = Config()
