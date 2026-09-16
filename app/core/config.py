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
    debug: bool = False
    db_user: str = ""
    db_password: SecretStr = SecretStr("")
    db_name: str = "inwebstigator.db"

    email: str = ""
    email_password: SecretStr = SecretStr("")
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465

    web_crawler_default_max_pages: int = 50000
    web_crawler_default_delay: float = 1
    web_crawler_max_delay: float = 10
    web_crawler_default_concurrent: int = 5
    web_crawler_batch_402_threshold_seconds: int = 50

    fetch_site_retry_max_attempts: int = 5
    fetch_site_retry_min_wait_seconds: int = 2
    fetch_site_retry_max_wait_seconds: int = 15
    fetch_site_retry_multiplier: int = 1

    email_retry_max_attempts: int = 3
    email_retry_min_wait_seconds: int = 2
    email_retry_max_wait_seconds: int = 15
    email_retry_multiplier: int = 1

    @property
    def db_url(self) -> str:
        return f"sqlite:///./{self.db_name}"

    @property
    def test_db_url(self) -> str:
        return "sqlite:///:memory:"


config = Config()
