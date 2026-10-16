"""模型工厂：按「角色」创建聊天模型，屏蔽不同厂商 SDK 的差异。

调用方只写 get_chat_model("extract")，具体用哪家、哪个模型、什么参数，全部由
config/models.yaml 决定。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from cooker.settings import get_settings

Role = Literal["agent", "router", "planner", "extract"]


class EndpointConfig(BaseModel):
    base_url_env: str
    api_key_env: str
    thinking_switch: str | None = None


class ProviderConfig(BaseModel):
    adapter: Literal["qwen", "deepseek", "openai"]
    endpoint: str
    roles: dict[str, str]


class RoleOptions(BaseModel):
    temperature: float = 0.3
    thinking: bool = False
    timeout: float = 60
    max_retries: int = 2


class EmbeddingConfig(BaseModel):
    endpoint: str
    model: str
    dimensions: int
    batch_size: int = 10
    timeout: float = 30


class RerankConfig(BaseModel):
    endpoint: str
    model: str
    path: str
    timeout: float = 20


class ModelsConfig(BaseModel):
    default_provider: str
    fallback_provider: str | None = None
    endpoints: dict[str, EndpointConfig]
    providers: dict[str, ProviderConfig]
    role_options: dict[str, RoleOptions] = {}
    embedding: EmbeddingConfig | None = None
    rerank: RerankConfig | None = None


class ModelConfigError(RuntimeError):
    """配置缺失或不合法（例如没有填 API Key）。"""


@lru_cache
def load_models_config() -> ModelsConfig:
    path = get_settings().models_config
    return ModelsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _read_secret(name: str) -> str | None:
    """优先读进程环境变量，其次读 .env；不把 .env 写回 os.environ。"""
    value = os.environ.get(name) or dotenv_values(get_settings().model_config["env_file"]).get(name)
    return value or None


def resolve_provider(provider: str | None = None) -> str:
    cfg = load_models_config()
    name = provider or get_settings().provider or cfg.default_provider
    if name not in cfg.providers:
        raise ModelConfigError(f"未知厂商 {name!r}，可选：{sorted(cfg.providers)}")
    return name


def endpoint_credentials(endpoint_name: str) -> tuple[str, str]:
    """返回 (base_url, api_key)；缺失时抛 ModelConfigError。"""
    endpoint = load_models_config().endpoints[endpoint_name]
    api_key = _read_secret(endpoint.api_key_env)
    base_url = _read_secret(endpoint.base_url_env)
    if not api_key or not base_url:
        raise ModelConfigError(f"接入点 {endpoint_name!r} 缺少 {endpoint.api_key_env} / {endpoint.base_url_env}")
    return base_url, api_key


def is_provider_available(provider: str) -> bool:
    cfg = load_models_config()
    endpoint = cfg.endpoints[cfg.providers[provider].endpoint]
    return bool(_read_secret(endpoint.api_key_env) and _read_secret(endpoint.base_url_env))


def get_chat_model(role: Role, provider: str | None = None, **overrides: Any) -> BaseChatModel:
    """创建单个厂商的聊天模型（不带降级）。overrides 可覆盖 role_options 中的参数。"""
    cfg = load_models_config()
    provider = resolve_provider(provider)
    pcfg = cfg.providers[provider]
    if role not in pcfg.roles:
        raise ModelConfigError(f"厂商 {provider!r} 未配置角色 {role!r}")
    endpoint = cfg.endpoints[pcfg.endpoint]
    api_key = _read_secret(endpoint.api_key_env)
    base_url = _read_secret(endpoint.base_url_env)
    if not api_key or not base_url:
        raise ModelConfigError(
            f"厂商 {provider!r} 缺少 {endpoint.api_key_env} / {endpoint.base_url_env}，请在 .env 中配置"
        )

    opts = cfg.role_options.get(role, RoleOptions()).model_copy(update=overrides)
    common: dict[str, Any] = {
        "model": pcfg.roles[role],
        "api_key": api_key,
        "temperature": opts.temperature,
        "timeout": opts.timeout,
        "max_retries": opts.max_retries,
    }
    extra_body = {endpoint.thinking_switch: opts.thinking} if endpoint.thinking_switch else None

    if pcfg.adapter == "qwen":
        from langchain_qwq import ChatQwen

        # ChatQwen 自己按 enable_thinking 字段写 extra_body
        return ChatQwen(api_base=base_url, enable_thinking=opts.thinking, **common)
    if pcfg.adapter == "deepseek":
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(api_base=base_url, extra_body=extra_body, **common)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(base_url=base_url, extra_body=extra_body, **common)


def build_runnable(
    role: Role,
    transform: Callable[[BaseChatModel], Runnable] = lambda m: m,
    provider: str | None = None,
    **overrides: Any,
) -> Runnable:
    """创建带降级的 Runnable：主厂商失败时自动切到 fallback_provider。

    注意 transform（如 bind_tools / with_structured_output）必须在 with_fallbacks
    之前分别作用到每个候选模型上：RunnableWithFallbacks 本身没有 bind_tools 方法，
    而且主备模型各自需要绑定同一套工具 / 输出结构。
    """
    cfg = load_models_config()
    primary_name = resolve_provider(provider)
    primary = transform(get_chat_model(role, primary_name, **overrides))

    fb = cfg.fallback_provider
    if not fb or fb == primary_name or not is_provider_available(fb):
        return primary
    return primary.with_fallbacks([transform(get_chat_model(role, fb, **overrides))])
