"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Central configuration — reads from .env file.

    Required keys (SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY) must be set
    for production use.  In development the app still starts so you can
    inspect the OpenAPI docs even if they are placeholders.
    """

    # Supabase
    supabase_url: str = Field(
        default="https://placeholder.supabase.co",
        description="Supabase project URL",
    )
    supabase_key: str = Field(
        default="placeholder-key",
        description="Supabase anon/service key",
    )

    # Gemini
    gemini_api_key: str = Field(
        default="placeholder-key",
        description="Gemini API key",
    )

    # SMTP
    smtp_host: str = Field(default="smtp.gmail.com")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_pass: str = Field(default="")
    notification_email_to: str = Field(default="")

    # App
    app_base_url: str = Field(default="http://localhost:8000")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


# Locate the .env relative to the project root (one level up from app/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    settings = Settings(_env_file=str(_env_path))
else:
    settings = Settings()
