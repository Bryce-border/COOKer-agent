"""麦当劳 MCP 配置：只从本机 config/mcd-mcp.local.env 读取，不经过对话、前端或日志。"""

from __future__ import annotations

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cooker.settings import PROJECT_ROOT

OFFICIAL_URL = "https://mcp.mcd.cn"


class McdSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / "config/mcd-mcp.local.env",
        env_file_encoding="utf-8", env_prefix="MCD_MCP_", extra="ignore",
    )
    url: str = OFFICIAL_URL
    token: SecretStr = SecretStr("")

    @field_validator("url")
    @classmethod
    def official_endpoint(cls, value: str) -> str:
        # 防止配置笔误把账户凭据发送到第三方地址
        if value.rstrip("/") != OFFICIAL_URL:
            raise ValueError("仅支持麦当劳官方 MCP 端点")
        return value

    @field_validator("token")
    @classmethod
    def token_only(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value().strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return SecretStr(token)
