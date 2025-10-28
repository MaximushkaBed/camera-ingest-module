from pydantic import BaseModel, Field
from typing import Optional, Literal

class Camera(BaseModel):
    id: str = Field(..., description="Unique camera ID")
    source_type: Literal["rtsp", "http_push"] = Field(..., description="Source type of the camera")
    source_url: Optional[str] = Field(None, description="RTSP URL or other source identifier")
    status: str = Field("disconnected", description="Current status of the camera connection")

class CameraCreate(BaseModel):
    id: str
    source_type: Literal["rtsp", "http_push"]
    source_url: Optional[str] = None
