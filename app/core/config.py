import secrets
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr
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

    server_host: str = "127.0.0.1"
    server_preferred_port: int = 48731
    allowed_hosts: list[str] = ["127.0.0.1", "localhost"]
    api_token: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    api_token_required: bool = True

    log_file_max_bytes: int = 1_000_000
    log_file_backup_count: int = 3

    email: str = ""
    email_password: SecretStr = SecretStr("")
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_timeout_seconds: int = 30
    imap_host: str = ""
    imap_port: int = 993
    email_bounce_wait_seconds: int = 30
    email_bounce_poll_seconds: int = 3
    email_time_zone: str = ""

    web_crawler_user_agent: str = "Mozilla/5.0 (compatible; Inwebstigator/0.1; website change monitor)"
    web_crawler_default_max_pages: int = 50_000
    web_crawler_default_delay: float = 0.5
    web_crawler_max_delay: float = 3
    web_crawler_default_concurrent: int = 5
    web_crawler_min_concurrent: int = 1
    web_crawler_batch_403_threshold: int = 50
    web_crawler_max_failed_attempts_at_min_speed: int = 3
    web_crawler_max_missing_pages_ratio: float = 0.5
    web_crawler_min_known_pages_to_check_missing: int = 20
    web_crawler_missing_pages_scans_before_accepting: int = 3

    fetch_site_retry_max_attempts: int = 5
    fetch_site_retry_min_wait_seconds: int = 2
    fetch_site_retry_max_wait_seconds: int = 15
    fetch_site_retry_multiplier: int = 1
    fetch_site_max_retry_after_seconds: int = 60

    critical_page_alert_after_failures: int = 2
    critical_page_stand_in_failures_before_accepting: int = 3

    diff_checker_text_similarity_threshold: float = 0.6
    diff_checker_max_comparisons_per_block: int = 20
    diff_checker_min_content_ratio: float = 0.3
    diff_checker_min_content_chars: int = 200

    email_retry_max_attempts: int = 3
    email_retry_min_wait_seconds: int = 2
    email_retry_max_wait_seconds: int = 15
    email_retry_multiplier: int = 1

    website_cooldown_hours_after_throttle: int = 24
    website_cooldown_hours_after_unreachable: int = 2

    scans_kept_per_website: int = 7

    scheduler_minimum_days_between_scans: float = 0.5
    scheduler_default_days_between_scans: float = 1
    scheduler_default_days_between_health_checks: float = 7
    scheduler_scan_due_tolerance_minutes: int = 60

    @property
    def log_path(self) -> Path:
        """The app's log file, in its data folder so testers' copies of the app keep a record of what went wrong."""
        return self.user_data_dir / "logs" / f"{self.app_name}.log"

    @property
    def db_url(self) -> str:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def test_db_url(self) -> str:
        return "sqlite:///:memory:"


config = Config()
