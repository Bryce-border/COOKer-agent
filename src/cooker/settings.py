"""全局配置：路径来自环境变量 / .env，模型配置来自 config/models.yaml。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="COOKER_",
        extra="ignore",
    )

    howtocook_dir: Path = PROJECT_ROOT / "HowToCook"
    data_dir: Path = PROJECT_ROOT / "data"
    models_config: Path = PROJECT_ROOT / "config" / "models.yaml"
    # 覆盖 models.yaml 里的 default_provider
    provider: str | None = None

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


@lru_cache
def get_settings() -> Settings:
    return Settings()
