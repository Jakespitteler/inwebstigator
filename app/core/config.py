import sys
import uuid

from dotenv import load_dotenv
from pathlib import Path
from pydantic import SecretStr
from pydantic_settings import BaseSettings

load_dotenv()

def get_database_path(db_name: str) -> Path:
    """Return the writable database path for the current environment."""
    if getattr(sys, "frozen", False):
        app_data = Path.home() / "AppData" / "Local" / "Inwebstigator"
        app_data.mkdir(parents=True, exist_ok=True)
        return app_data / db_name
    
    return Path.cwd() / db_name

class Config(BaseSettings):
    """Application settings and environment configuration manager.

    Loads configuration variables from environment files or environment variables,
    providing default fallback values and sensitive credential handling via Pydantic's
    SecretStr type.
    """

    app_name: str = "inwebstigator"
    automatic_scans: bool = True

    user_id: uuid.UUID | None = None

    db_user: str = ""
    db_password: SecretStr = SecretStr("")
    db_name: str = "inwebstigator.db"

    email: str = ""
    email_password: SecretStr = SecretStr("")
    smtp_host: str = "smtp.gmail.com"
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

    text_similarity_threshold: float = 0.6  # TODO may have to drop to 0

    email_retry_max_attempts: int = 3
    email_retry_min_wait_seconds: int = 2
    email_retry_max_wait_seconds: int = 15
    email_retry_multiplier: int = 1

    @property
    def db_url(self) -> str:
        return f"sqlite:///{get_database_path(self.db_name).as_posix()}"

    @property
    def test_db_url(self) -> str:
        return "sqlite:///:memory:"


config = Config()
