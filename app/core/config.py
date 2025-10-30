from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # --- Redis ---
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    ENABLE_PERSON_DETECTION: bool = True

    # --- Настройки детектора ---
    PERSON_COOLDOWN_SECONDS: int = 10
    YOLO_CONFIDENCE_THRESHOLD: float = 0.65

    # Минимальная площадь движения в пикселях, чтобы сработал "дешевый" детектор
    MOTION_MIN_AREA: int = 1500 
    # Как часто (в секундах) можно запускать YOLO для одной камеры, даже если есть движение
    YOLO_TRIGGER_COOLDOWN: int = 3

    # --- Путь к файлу с камерами ---
    CAMERA_DB_FILE: str = "cameras.json"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()