import pytest

from conftest import make_recipe
from cooker.recipes.ingredients import dish_key, matches, normalize
from cooker.recipes.recommend import recommend_by_ingredients
from cooker.recipes.scaling import scale_recipe, shopping_list
from cooker.recipes.store import RecipeFilter, RecipeNotFound, RecipeStore


def test_normalize_and_matches():
    assert normalize("西红柿（大）") == "番茄"
    assert matches("鸡翅中", "鸡翅") and matches("鸡翅", "鸡翅中")
    assert not matches("鱼", "鱿鱼")  # 单字只做精确匹配
    assert dish_key("西红柿炒鸡蛋") == dish_key("番茄炒蛋的做法")


def test_find_by_synonym_and_suggestions(real_store):
    assert real_store.find("番茄炒蛋").name == "西红柿炒鸡蛋"
    assert real_store.find("meat_dish/可乐鸡翅").name == "可乐鸡翅"
    with pytest.raises(RecipeNotFound) as e:
        real_store.find("红烧")
    assert len(e.value.suggestions) > 1  # 多个包含匹配时不擅自选一个


def test_filter():
    a = make_recipe("凉拌香菜", raw=["香菜", "生抽"], allergens=["大豆"])
    b = make_recipe("红烧肉", category="meat_dish", vegetarian=False, difficulty=4)
    store = RecipeStore([a, b], [])
    assert store.filter(RecipeFilter(vegetarian=True)) == [a]
    assert store.filter(RecipeFilter(max_difficulty=3)) == [a]
    assert store.filter(RecipeFilter(exclude_allergens=["大豆"])) == [b]
    assert store.filter(RecipeFilter(exclude_ingredients=["香菜"])) == [b]
    assert store.filter(RecipeFilter(categories=["meat_dish"])) == [b]


def _ing(name, amount, unit, scaling, **kw):
    return {"name": name, "amount": amount, "unit": unit, "scaling": scaling, **kw}


def test_scale_recipe_rules():
    r = make_recipe("测试", serving=2.0, ingredients=[
        _ing("五花肉", 300, "g", "per_serving"),
        _ing("鸡蛋", 1, "个", "per_person"),
        _ing("洋葱", 0.3, "个", "per_serving"),
        _ing("酱汁", 100, "ml", "fixed"),
        _ing("盐", None, None, "to_taste"),
    ])
    s = scale_recipe(r, 5)  # 一份 2 人 → 2.5 份
    got = {i.name: i.amount for i in s.items}
    assert got == {"五花肉": 750, "鸡蛋": 5, "洋葱": 1.0, "酱汁": 100, "盐": None}  # 0.75 个洋葱向上取到 1
    assert any("固定用量" in w for w in s.warnings)
    with pytest.raises(ValueError):
        scale_recipe(r, 0)


def test_shopping_list_merges_and_rounds():
    a = make_recipe("甲", serving=1.0, ingredients=[_ing("西红柿", 1.5, "个", "per_serving"),
                                                     _ing("盐", 2, "g", "per_serving")])
    b = make_recipe("乙", serving=1.0, ingredients=[_ing("番茄", 200, "g", "per_serving"),
                                                     _ing("番茄", 1, "个", "per_serving"),
                                                     _ing("香菜", 5, "g", "per_serving", optional=True)])
    sl = shopping_list([scale_recipe(a, 1), scale_recipe(b, 1)])
    lines = {line.name: line for line in sl.groceries}
    assert lines["番茄"].amount == "3 个 + 200 g"  # 同义词合并；1.5+1=2.5 个 → 采购取整 3；不同单位分列
    assert lines["番茄"].used_in == ["甲", "乙"]
    assert "香菜" not in lines                      # 可选原料省略
    assert [line.name for line in sl.pantry] == ["盐"]  # 调料单独归类


def test_recommend_diversifies_across_ingredients():
    recipes = [
        make_recipe("蛋A", main=["鸡蛋", "番茄"],
                    ingredients=[_ing("鸡蛋", 2, "个", "per_serving"), _ing("番茄", 1, "个", "per_serving")]),
        make_recipe("蛋B", main=["鸡蛋"]),
        make_recipe("蛋C", main=["鸡蛋"]),
        make_recipe("土豆丝", main=["土豆"]),
        make_recipe("牛肉", main=["牛肉", "洋葱"]),  # 没有任何主料 → 不推荐
    ]
    out = recommend_by_ingredients(RecipeStore(recipes, []), ["鸡蛋", "西红柿", "土豆"], limit=3)
    assert [m.name for m in out] == ["蛋A", "土豆丝", "蛋B"]  # 第二名让给能用上土豆的菜
    assert out[0].matched == ["鸡蛋", "番茄"]
    with pytest.raises(ValueError):
        recommend_by_ingredients(RecipeStore(recipes, []), [" "])
