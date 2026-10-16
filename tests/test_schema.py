from cooker.ingest.allergens import derive_allergens
from cooker.ingest.extractor import PROMPT_VERSION, cache_key
from cooker.ingest.schema import Ingredient, RecipeFacts


def _facts(**kw):
    base = dict(ingredients=[Ingredient(name="x", scaling="fixed", amount=1)], tools=[], main_ingredients=[],
                flavors=[], methods=[], is_vegetarian=True)
    return RecipeFacts(**{**base, **kw})


def test_tag_synonyms_normalized_and_unknown_dropped():
    f = _facts(flavors=["微辣", "酸辣", "咸鲜", "不存在"], methods=["拌", "炒"])
    assert f.flavors == ["辣", "酸", "咸鲜"]
    assert f.methods == ["凉拌", "炒"]


def test_ingredient_without_amount_is_to_taste():
    i = Ingredient(name="尖椒", scaling="per_person")
    assert i.scaling == "to_taste"


def test_unit_normalization():
    assert Ingredient(name="鸭肉", amount=1, unit="kg", scaling="fixed").model_dump(include={"amount", "unit"}) == {"amount": 1000, "unit": "g"}
    assert Ingredient(name="水", amount=200, unit="毫升", scaling="fixed").unit == "ml"
    r = Ingredient(name="油", amount=15, amount_max=10, unit="ml", scaling="fixed")
    assert (r.amount, r.amount_max) == (10, 15)


def test_allergens_rules():
    assert derive_allergens(["冬瓜", "冰糖"]) == []
    assert derive_allergens(["全脂牛奶", "红茶"]) == ["乳制品"]
    assert derive_allergens(["鱼露"]) == []           # 排除词
    assert derive_allergens(["鱿鱼"]) == ["海鲜"]     # 不误判为鱼
    assert set(derive_allergens(["生抽"])) == {"大豆", "麸质"}


def test_cache_key_depends_on_content_and_prompt_version():
    class R:  # 只需要 content_hash 属性
        content_hash = "abc"
    k1 = cache_key(R())
    R.content_hash = "abd"
    assert cache_key(R()) != k1
    assert PROMPT_VERSION
