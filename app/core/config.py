from dotenv import load_dotenv
from pydantic import SecretStr
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

    @property
    def db_url(self) -> str:
        return f"sqlite:///./{self.db_name}"

    @property
    def test_db_url(self) -> str:
        return "sqlite:///:memory:"


config = Config()
