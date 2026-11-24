from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = "sqlite:///./gilead.db"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 480
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    seed_demo_data: bool = True
    seed_sample_data: bool = False   # frontend testing: runs, saved items, sharing, scoring (app/seed_samples.py)
    # AI Scientific Search service (AWS API Gateway). Empty = research runs fail with "not configured"; AI Rephrase 503.
    ai_search_base_url: str = ""
    ai_search_timeout_seconds: float = 90
    ai_search_api_key: str = ""      # only if the gateway is protected with an API key (sent as x-api-key)

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
