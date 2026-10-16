"""模型工厂测试：只构造对象，不发网络请求。"""
import pytest
from langchain_core.runnables import RunnableWithFallbacks

from cooker.llm import factory


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://example.invalid/compatible-mode/v1")


def test_default_provider_is_qwen():
    m = factory.get_chat_model("extract")
    assert type(m).__name__ == "ChatQwen"
    assert m.model_name == factory.load_models_config().providers["qwen"].roles["extract"]
    assert m.enable_thinking is False


def test_deepseek_on_bailian_disables_thinking():
    m = factory.get_chat_model("agent", "deepseek")
    assert type(m).__name__ == "ChatDeepSeek"
    assert m.extra_body == {"enable_thinking": False}


def test_unknown_provider_and_missing_key(monkeypatch):
    with pytest.raises(factory.ModelConfigError):
        factory.get_chat_model("agent", "nope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(factory, "_read_secret", lambda name: None)
    with pytest.raises(factory.ModelConfigError, match="DASHSCOPE_API_KEY"):
        factory.get_chat_model("agent")


def test_build_runnable_applies_transform_before_fallback():
    seen = []
    r = factory.build_runnable("extract", transform=lambda m: (seen.append(type(m).__name__), m)[1])
    assert isinstance(r, RunnableWithFallbacks)
    assert seen == ["ChatQwen", "ChatDeepSeek"]  # 主备模型各自被 transform


def test_agent_model_forces_tool_choice_on_both_providers():
    """回归：langchain-qwq 会把 tool_choice=required 改写成 auto，必须在绑定后覆盖。"""
    from cooker.agent.graph import get_agent_model

    get_agent_model.cache_clear()
    r = get_agent_model("qwen", True)
    assert r.runnable.kwargs["tool_choice"] == "required"
    assert all(f.kwargs["tool_choice"] == "required" for f in r.fallbacks)
    assert "tool_choice" not in get_agent_model("qwen", False).runnable.kwargs
    get_agent_model.cache_clear()
