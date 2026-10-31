"""连接器客户端中不依赖假服务的部分：真实传输层的 httpx 工厂、HTTP 错误码提示。"""

import asyncio

import pytest

from cooker.connectors import client
from cooker.connectors.base import ConnectorError, Credentials
from cooker.luckin.connector import CONNECTOR as LUCKIN


def test_transport_factory_accepts_fastmcp_kwargs_and_records_errors():
    """fastmcp 会以 follow_redirects=True 等额外参数调用工厂；签名不兼容会让所有真实调用失败（曾发生过）。"""
    statuses: list[int] = []
    transport = client.open_transport(LUCKIN, Credentials(LUCKIN.url, "t"), statuses=statuses)
    http = transport.httpx_client_factory(headers={}, auth=None, follow_redirects=True)
    hook = http.event_hooks["response"][-1]

    class Resp:
        status_code = 401

    asyncio.run(hook(Resp()))
    assert statuses == [401]
    asyncio.run(http.aclose())


@pytest.mark.parametrize("status, text", [(401, "密钥无效或已过期"), (429, "请求过于频繁")])
def test_http_status_becomes_clear_message(monkeypatch, status, text):
    def failing_transport(conn, creds, *, statuses):
        statuses.append(status)
        raise RuntimeError("Server returned an error response")  # SDK 只给出这样的笼统信息

    monkeypatch.setattr(client, "open_transport", failing_transport)
    monkeypatch.setattr(client, "load_credentials", lambda conn: Credentials(conn.url, "t"))
    with pytest.raises(ConnectorError, match=text):
        asyncio.run(client.call(LUCKIN, "queryShopList", {"longitude": 1.0, "latitude": 1.0}))
