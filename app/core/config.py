from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres123@localhost:5432/ams_db"
    CORS_ORIGINS: str = "http://localhost:3000"
    APP_ENV: str = "development"

    # Login. Switched OFF for now (development); set AUTH_REQUIRED=true in .env when deploying.
    AUTH_REQUIRED: bool = False
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    SECRET_KEY: str = ""        # optional; when empty a random key is created once (backend/.secret_key)

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    class Config:
        env_file = ".env"


settings = Settings()
