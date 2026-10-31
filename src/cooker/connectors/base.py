"""连接器：描述一个外部 MCP 服务（麦当劳、瑞幸……）需要的全部差异。

通用流程（调用客户端、确认卡、台账、工具生成）只写一次，每个服务只提供一个 Connector：
官方地址、凭据文件、读写白名单、写工具的确认卡生成函数、返回值的服务特有处理。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Awaitable, Callable, NamedTuple

from dotenv import dotenv_values

from cooker.settings import PROJECT_ROOT


class ConnectorError(RuntimeError):
    """消息可直接展示给用户（不含堆栈、凭据、内部细节）。"""


class Unknown(ConnectorError):
    """写请求已发出但没拿到确定结果：不能证明没执行，禁止自动重试。"""


class Refuse(Exception):
    """前置条件不满足：不弹确认卡，把原因告诉模型（由它转告用户或补全参数）。"""


@dataclass
class Card:
    title: str
    rows: list[tuple[str, str]]
    warnings: list[str] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)  # 商品缩略图 [{name, url}]，不参与指纹
    facts: Any = None  # 参与指纹的实时数据（价格、消耗、订单状态）
    args: dict | None = None  # 实际执行用的参数；None 表示原样使用模型给的参数（见瑞幸的券码透传）


class Credentials(NamedTuple):
    url: str
    token: str


CardBuilder = Callable[["Connector", dict], Awaitable[Card]]


def default_business_error(payload: Any) -> str | None:
    """服务端明确拒绝时返回原因（原样转述）；正常返回 None。适用于 {success, code, msg/message, data} 结构。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    status = data.get("status") if isinstance(data, dict) else None
    if isinstance(status, dict) and status.get("code") not in (None, "SUCCESS"):
        return str(status.get("message") or status.get("code"))
    if payload.get("success") is False or payload.get("code") not in (None, 0, 200, "0", "200", "SUCCESS"):
        return str(payload.get("message") or payload.get("msg") or "服务端返回失败")
    return None


def keep(name: str, payload: Any) -> Any:
    return payload


def data_of(payload: Any) -> Any:
    return payload.get("data", payload) if isinstance(payload, dict) else payload


@dataclass(frozen=True, eq=False)
class Connector:
    key: str  # 短名，出现在确认卡、台账、前端图标里：mcd / luckin
    label: str  # 给用户看的名字：麦当劳 / 瑞幸
    skill: str  # 对应 skills/<skill>/SKILL.md 的 name
    url: str  # 唯一允许的官方端点：防止配置笔误把凭据发到第三方
    env_prefix: str  # 环境变量 / 配置文件前缀，如 MCD_MCP_ → MCD_MCP_TOKEN
    env_file: str  # 相对项目根，如 config/mcd-mcp.local.env（gitignore）
    package_dir: Path  # 放 tool_schemas.json、host_adapter.md 的目录
    read_tools: frozenset[str]
    write_tools: frozenset[str]
    cards: dict[str, CardBuilder]  # 每个写工具一个确认卡生成函数（必须覆盖全部写工具）
    app_hint: str  # 结果未知时让用户去哪里核实，如「麦当劳 App」
    image_hosts: frozenset[str] = frozenset()  # 允许在界面显示的官方图床域名（含子域名）
    business_error: Callable[[Any], str | None] = default_business_error
    # 交给模型前的服务特有处理（单位换算、去掉不该展示的字段），返回新的 payload
    present: Callable[[str, Any], Any] = keep

    def __post_init__(self):
        assert set(self.cards) == set(self.write_tools), f"{self.key}: 每个写工具都必须有确认卡"
        assert not self.read_tools & self.write_tools

    @cached_property
    def schemas(self) -> dict[str, dict]:
        """快照中、且在白名单内的工具：name → {name, description, input_schema}。"""
        items = json.loads((self.package_dir / "tool_schemas.json").read_text(encoding="utf-8"))
        return {t["name"]: t for t in items if t["name"] in self.read_tools | self.write_tools}

    @property
    def tool_names(self) -> frozenset[str]:
        return self.read_tools | self.write_tools

    def host_adapter(self) -> str:
        return (self.package_dir / "host_adapter.md").read_text(encoding="utf-8")

    def credentials(self) -> Credentials:
        """读凭据：环境变量优先，其次本机配置文件。凭据只经过这里与 client.py，不进对话、前端、日志。"""
        values = {**dotenv_values(PROJECT_ROOT / self.env_file),
                  **{k: v for k, v in os.environ.items() if k.startswith(self.env_prefix)}}
        url = (values.get(self.env_prefix + "URL") or self.url).rstrip("/")
        if url != self.url.rstrip("/"):
            raise ConnectorError(f"{self.label}配置无效：只允许官方端点 {self.url}")
        token = (values.get(self.env_prefix + "TOKEN") or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            example = self.env_file.replace(".local.env", ".example.env")
            raise ConnectorError(f"尚未配置{self.label} MCP 密钥：复制 {example} 为 {Path(self.env_file).name} 并填写 Token")
        return Credentials(self.url, token)
