from pydantic import BaseModel, Field
from typing import Optional, Literal

# Типы источников, которые поддерживает наша система
SourceType = Literal["rtsp", "mjpeg", "http_push", "onvif"]

class CameraBase(BaseModel):
    id: str = Field(..., description="Уникальный ID камеры, например 'cam_001'")
    source_type: SourceType = Field(..., description="Тип источника: rtsp, mjpeg, http_push, onvif")

class CameraCreate(CameraBase):
    # Для потоковых камер (rtsp, mjpeg) URL может быть предоставлен напрямую
    source_url: Optional[str] = Field(None, description="Прямая ссылка на видеопоток (для rtsp и mjpeg)")
    
    # Новые поля для ONVIF-автообнаружения
    ip_address: Optional[str] = Field(None, description="IP-адрес или хостнейм камеры (для onvif)")
    onvif_port: int = Field(80, description="Порт для ONVIF-подключения (обычно 80)")
    username: Optional[str] = Field(None, description="Имя пользователя для камеры (для onvif)")
    password: Optional[str] = Field(None, description="Пароль для камеры (для onvif)")

class Camera(CameraBase):
    # ИСПРАВЛЕНИЕ: Добавляем все недостающие поля сюда
    source_url: Optional[str] = None
    ip_address: Optional[str] = None
    onvif_port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None # Хотя пароль лучше не хранить, для простоты оставим
    
    status: str = "disconnected"