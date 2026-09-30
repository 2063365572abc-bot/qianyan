from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://qianyan:qianyan@127.0.0.1:5432/qianyan"
    app_public_url: str = "http://localhost:5173"
    session_secret: str = ""
    owner_password_hash: str = ""
    nebius_api_key: str = ""
    nebius_model_id: str = "nvidia/nemotron-3-super-120b-a12b"
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    github_token: str = ""
    vercel_token: str = ""
    wecom_corp_id: str = ""
    wecom_agent_id: str = ""
    wecom_app_secret: str = ""
    wecom_user_id: str = ""
    wecom_callback_token: str = ""
    wecom_encoding_aes_key: str = ""
    worker_interval: float = 5
    observation_interval: int = 300
    owner_daily_calls: int = 50
    demo_daily_calls: int = 10
    global_demo_daily_calls: int = 100
    daily_token_limit: int = 500_000

    @property
    def production(self):
        return self.app_env == "production"


@lru_cache
def config():
    return Config()
