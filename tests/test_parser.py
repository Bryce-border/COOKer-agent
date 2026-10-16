from pathlib import Path

import pytest

from cooker.ingest.parser import RecipeFormatError, iter_recipe_files, parse_recipe


def _write(tmp_path: Path, rel: str, text: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


GOOD = """# 测试菜的做法

![成品(大图)](./成品(大图).jpg)

一道测试菜。

预估烹饪难度：★★★

预估卡路里：300 大卡

## 必备原料和工具

- 鸡蛋
- 生抽

## 计算

- 鸡蛋 2 个

## 操作

### 准备

1. 打蛋
  - 加盐
2. 搅拌 ![步骤](./步骤.jpg)

### 烹饪

1. 下锅

## 附加内容

无
"""


def test_parse_minimal_recipe(tmp_path):
    md = _write(tmp_path, "dishes/meat_dish/测试菜/测试菜.md", GOOD)
    r = parse_recipe(md, tmp_path)
    assert r.id == "meat_dish/测试菜"
    assert r.category_name == "荤菜"
    assert (r.difficulty, r.calories) == (3, 300)
    assert r.description == "一道测试菜。"
    assert r.ingredients_raw == ["鸡蛋", "生抽"]
    # 文件名带括号 + 步骤内图片
    assert r.images == ["dishes/meat_dish/测试菜/成品(大图).jpg", "dishes/meat_dish/测试菜/步骤.jpg"]
    assert [g.title for g in r.steps] == ["准备", "烹饪"]
    assert r.steps[0].steps[0] == "打蛋（加盐）"  # 子列表并入上一步


@pytest.mark.parametrize(
    "broken, msg",
    [
        (GOOD.replace("# 测试菜的做法", "# 测试菜"), "一级标题"),
        (GOOD.replace("## 计算", "## 用量"), "二级标题"),
        (GOOD.replace("预估烹饪难度：★★★", ""), "难度"),
    ],
)
def test_format_violation_raises(tmp_path, broken, msg):
    md = _write(tmp_path, "dishes/meat_dish/测试菜/测试菜.md", broken)
    with pytest.raises(RecipeFormatError, match=msg):
        parse_recipe(md, tmp_path)


def test_unknown_category_raises(tmp_path):
    md = _write(tmp_path, "dishes/unknown/测试菜.md", GOOD)
    with pytest.raises(RecipeFormatError, match="未知分类"):
        parse_recipe(md, tmp_path)


def test_all_upstream_recipes_parse(repo_root):
    """回归：上游全部菜谱都能解析，且 ID 唯一。"""
    recipes = [parse_recipe(p, repo_root) for p in iter_recipe_files(repo_root)]
    assert len(recipes) >= 350
    assert len({r.id for r in recipes}) == len(recipes)
    assert all(r.steps and r.ingredients_raw for r in recipes)
    for r in recipes:
        for img in r.images:
            assert img.startswith("http") or (repo_root / img).exists(), img
