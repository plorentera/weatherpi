from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    station_id: str = "meteo-001"
    sample_interval_seconds: int = 5
    collector: Dict[str, Any] = Field(default_factory=dict)
    telemetry: Dict[str, Any] = Field(default_factory=dict)
    remote_config: Dict[str, Any] = Field(default_factory=dict)
    updates: Dict[str, Any] = Field(default_factory=dict)
    security: Dict[str, Any] = Field(default_factory=dict)
    exports: Dict[str, Any] = Field(default_factory=dict)
    ui: Dict[str, Any] = Field(default_factory=dict)


class SecretStoreModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    telemetry_destinations: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    remote_config: Dict[str, Any] = Field(default_factory=dict)
    updates: Dict[str, Any] = Field(default_factory=dict)


class CredentialsUpdateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reader_username: str = "reader"
    reader_password: Optional[str] = None
    reader_password_confirm: Optional[str] = None
    admin_username: str = "admin"
    admin_password: Optional[str] = None
    admin_password_confirm: Optional[str] = None
