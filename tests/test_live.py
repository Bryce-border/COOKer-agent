"""真实调用 LLM 的端到端测试：pytest -m live"""
import pytest

from cooker.ingest.extractor import build_extractor, _messages
from cooker.ingest.parser import parse_recipe

pytestmark = pytest.mark.live


@pytest.mark.parametrize("provider", ["qwen", "deepseek"])
def test_extract_one_recipe(repo_root, provider):
    recipe = parse_recipe(repo_root / "dishes/meat_dish/尖椒炒牛肉.md", repo_root)
    facts, raw = build_extractor(provider).invoke(_messages(recipe))
    names = {i.name for i in facts.ingredients}
    assert "牛肉" in names
    assert facts.is_vegetarian is False
    assert raw.response_metadata.get("model_name")
