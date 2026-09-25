import uuid
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

    user_id: uuid.UUID | None = None

    db_name: str = f"{app_name}.db"

    email: str = ""
    email_password: SecretStr = SecretStr("")
    smtp_host: str = ""
    smtp_port: int = 465

    web_crawler_default_max_pages: int = 50000
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

    text_similarity_threshold: float = 0.6  # TODO May have to change to 0

    email_retry_max_attempts: int = 3
    email_retry_min_wait_seconds: int = 2
    email_retry_max_wait_seconds: int = 15
    email_retry_multiplier: int = 1

    @property
    def db_url(self) -> str:
        user_data_dir = Path.home() / f".{self.app_name}"
        user_data_dir.mkdir(parents=True, exist_ok=True)
        db_path = user_data_dir / self.db_name
        return f"sqlite:///{db_path.as_posix()}"

    @property
    def test_db_url(self) -> str:
        return "sqlite:///:memory:"


config = Config()
