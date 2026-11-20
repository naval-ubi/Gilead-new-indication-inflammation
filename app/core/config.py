from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = "sqlite:///./gilead.db"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 480
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    seed_demo_data: bool = True

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
