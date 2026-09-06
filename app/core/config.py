from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings

load_dotenv()


class Config(BaseSettings):
    app_name: str = "DigitalHorizonScan"
    debug: bool = False
    email: str = ""
    email_password: SecretStr = SecretStr("")
    db_user: str = ""
    db_password: SecretStr = SecretStr("")
    db_name: str = "digital_horizon.db"

    # ------------------------------------------------------------------
    # Notifier
    #
    # Everything defaults to an example.com placeholder so a fresh checkout
    # runs the demo with no .env at all. Real sending is opt in: dry_run stays
    # True until someone deliberately sets DRY_RUN=false.
    # ------------------------------------------------------------------
    dry_run: bool = True
    site_name: str = "example.edu.au"
    # Comma separated in the environment. Read it through client_to_addresses,
    # which does the splitting -- a bare list[str] field would make
    # pydantic-settings try to JSON-decode the value.
    client_to: str = "client@example.com"
    from_addr: str = "sitewatch@example.com"
    smtp_host: str = "smtp.example.com"
    smtp_port: int = 587
    # Times in the report are shown in the client's timezone, not the server's.
    # The daily run at 06:00 UTC is 14:00 in Perth, and a report headed
    # "checked 06:00" when the client reads it over lunch invites a support
    # email. Any IANA name works; an unknown one falls back to UTC.
    report_timezone: str = "Australia/Perth"

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------
    # Wall clock time of the daily notification run, in report_timezone. A bad
    # value here means the client's report never goes out at a sane hour, so it
    # fails at startup rather than being quietly clamped.
    daily_run_hour: int = Field(default=6, ge=0, le=23)
    daily_run_minute: int = Field(default=0, ge=0, le=59)
    # The scrape loop still runs on a plain interval; it is not the thing the
    # client reads a timestamp off.
    scrape_interval_seconds: int = Field(default=3600, gt=0)
    # A parked send is retried with exponential backoff starting here, capped
    # at retry_max_delay_seconds, and given up on after retry_max_attempts.
    retry_base_delay_seconds: int = Field(default=900, gt=0)
    retry_max_delay_seconds: int = Field(default=86400, gt=0)
    retry_max_attempts: int = Field(default=5, gt=0)

    @property
    def client_to_addresses(self) -> list[str]:
        """CLIENT_TO split into individual recipients, blanks dropped."""
        return [address.strip() for address in self.client_to.split(",") if address.strip()]

    @property
    def db_url(self) -> str:
        return f"sqlite:///./{self.db_name}"

    @property
    def test_db_url(self) -> str:
        return "sqlite:///:memory:"


config = Config()
