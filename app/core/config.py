from dotenv import load_dotenv
from pydantic import SecretStr
from pydantic_settings import BaseSettings

load_dotenv()


class Config(BaseSettings):
    app_name: str = "inwebstigator"
    debug: bool = False
    email: str = ""
    email_password: SecretStr = SecretStr("")
    db_user: str = ""
    db_password: SecretStr = SecretStr("")
    db_name: str = "inwebstigator.db"
    web_crawler_max_pages: int = 5000
    web_crawler_batch_402_threshold_seconds = 20

    @property
    def db_url(self) -> str:
        return f"sqlite:///./{self.db_name}"

    @property
    def test_db_url(self) -> str:
        return "sqlite:///:memory:"


config = Config()
