from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    storage_path: Path = Path("runtime")
    session_hours: int = 12
    max_upload_mb: int = 1024
    max_projects: int = 80
    max_concurrent_jobs: int = 2
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    release_tag: str = "development"
    public_url: str = ""
    allowed_origins: str = ""
    app_name: str = "appnamexyz"
    app_tagline: str = ""
    app_description: str = "Map analysis, editing, and export."
    theme_color: str = "#171717"


settings = Settings()
