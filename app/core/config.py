from typing import Annotated

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode

load_dotenv()


class Config(BaseSettings):
    app_name: str = "DigitalHorizonScan"
    debug: bool = False
    # Also the From address. We send from whatever account we log in as.
    email: str = ""
    email_password: SecretStr = SecretStr("")
    db_user: str = ""
    db_password: SecretStr = SecretStr("")
    db_name: str = "digital_horizon.db"

    # notifier
    # - addresses and mail server start blank, filled in from .env
    # - blank is obvious, a fake one just mails nobody
    # - dry_run stays on until someone sets DRY_RUN=false
    dry_run: bool = True
    # Comma separated in .env, a list everywhere else.
    # NoDecode stops pydantic trying to read it as JSON first.
    client_to: Annotated[list[str], NoDecode] = []
    smtp_host: str = ""
    smtp_port: int = 587
    # Times in the report use the client's timezone, not the server's.
    # Any IANA name works, a bad one falls back to UTC.
    report_timezone: str = "Australia/Perth"

    # scheduler
    # What time the daily run goes out, in report_timezone.
    # A bad value fails at startup instead of being quietly fixed up.
    daily_run_hour: int = Field(default=6, ge=0, le=23)
    daily_run_minute: int = Field(default=0, ge=0, le=59)
    # The scrape loop just runs on an interval. Nobody reads a time off it.
    scrape_interval_seconds: int = Field(default=3600, gt=0)
    # Failed sends get retried. Backoff starts here, caps at
    # retry_max_delay_seconds, gives up after retry_max_attempts.
    retry_base_delay_seconds: int = Field(default=900, gt=0)
    retry_max_delay_seconds: int = Field(default=86400, gt=0)
    retry_max_attempts: int = Field(default=5, gt=0)

    @field_validator("client_to", mode="before")
    @classmethod
    def _split_recipients(cls, value: object) -> object:
        """Split CLIENT_TO on commas and drop blanks. A list comes through as is."""
        if isinstance(value, str):
            return [address.strip() for address in value.split(",") if address.strip()]
        return value

    @property
    def db_url(self) -> str:
        return f"sqlite:///./{self.db_name}"

    @property
    def test_db_url(self) -> str:
        return "sqlite:///:memory:"


config = Config()
