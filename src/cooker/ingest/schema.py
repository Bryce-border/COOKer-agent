"""菜谱数据模型。

分两层：
- ParsedRecipe：规则解析得到的字段，100% 来自原文、确定性的。
- RecipeFacts：LLM 从原文抽取的语义字段（用量、口味、过敏原等），可能有误差，
  只用于筛选、计算等辅助功能；面向用户的回答始终以原文为准。
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

CATEGORY_NAMES: dict[str, str] = {
    "vegetable_dish": "素菜",
    "meat_dish": "荤菜",
    "aquatic": "水产",
    "breakfast": "早餐",
    "staple": "主食",
    "semi-finished": "半成品加工",
    "soup": "汤与粥",
    "drink": "饮料",
    "condiment": "酱料和其它材料",
    "dessert": "甜品",
}


class StepGroup(BaseModel):
    title: str | None = Field(None, description="「操作」下的三级标题，没有则为 None")
    steps: list[str]


class ParsedRecipe(BaseModel):
    id: str = Field(description="稳定 ID：分类/菜名，如 meat_dish/可乐鸡翅")
    name: str
    category: str
    category_name: str
    source_path: str = Field(description="相对 HowToCook 仓库根目录的路径")
    content_hash: str
    description: str
    difficulty: int = Field(ge=1, le=5)
    calories: int | None
    images: list[str] = Field(description="仓库相对路径或外链 URL")
    ingredients_raw: list[str] = Field(description="「必备原料和工具」列表项原文")
    calculation_raw: str = Field(description="「计算」一节原文")
    steps: list[StepGroup]
    extras_raw: str = Field(description="「附加内容」一节原文")
    markdown: str = Field(description="完整原文，用于检索与引用")


class ParsedTip(BaseModel):
    id: str
    title: str
    category: str = Field(description="learn / advanced / general")
    source_path: str
    content_hash: str
    markdown: str


# ---------------- LLM 抽取 ----------------

Scaling = Literal["per_serving", "per_person", "fixed", "to_taste"]

# 标签词表：用 Literal 约束后会出现在 function calling 的 JSON Schema enum 中
Flavor = Literal["辣", "麻", "甜", "酸", "咸鲜", "清淡", "香浓"]
Method = Literal["炒", "煎", "炸", "蒸", "煮", "炖", "焖", "烤", "凉拌", "腌", "烧", "卤", "冲调"]
Allergen = Literal["花生", "坚果", "乳制品", "蛋", "海鲜", "鱼", "大豆", "麸质", "芝麻"]

# 模型仍可能输出近义词，入库前映射回词表；映射不了的丢弃，而不是让整条记录校验失败
_SYNONYMS: dict[str, list[str]] = {
    "微辣": ["辣"], "麻辣": ["麻", "辣"], "酸辣": ["酸", "辣"], "香辣": ["辣", "香浓"], "酸甜": ["酸", "甜"],
    "咸": ["咸鲜"], "鲜": ["咸鲜"], "咸香": ["咸鲜"], "清爽": ["清淡"], "浓郁": ["香浓"], "香": ["香浓"],
    "拌": ["凉拌"], "红烧": ["烧"], "爆炒": ["炒"], "焯水": ["煮"], "烘烤": ["烤"], "冲泡": ["冲调"], "调制": ["冲调"],
}

# 单位归一化：同义单位统一写法；质量单位换算到 g
_UNIT_ALIASES = {"克": "g", "G": "g", "毫升": "ml", "mL": "ml", "ML": "ml", "升": "l", "L": "l"}
_UNIT_FACTORS = {"kg": ("g", 1000), "千克": ("g", 1000), "公斤": ("g", 1000), "斤": ("g", 500), "l": ("ml", 1000)}


def _normalize_tags(values: list[str] | None, vocab: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for v in values or []:
        v = str(v).strip()
        for t in [v] if v in vocab else _SYNONYMS.get(v, []):
            if t in vocab and t not in out:
                out.append(t)
    return out


class Ingredient(BaseModel):
    name: str = Field(description="规范化的食材名，去掉形容词和用量，如「鸡蛋」「生抽」「五花肉」")
    amount: float | None = Field(None, description="数量的下限或确定值；适量/少许时为 null")
    amount_max: float | None = Field(None, description="给出范围时的上限，如 10-15ml 中的 15；否则为 null")
    unit: str | None = Field(None, description="单位：g、ml、个、根、瓣、片等；无则 null")
    scaling: Scaling = Field(
        description=(
            "用量如何随人数变化：per_person=每人用量；per_serving=每份用量（份的人数见 serving_size_people）；"
            "fixed=与份数无关的固定用量（含一次性制作的基础量）；to_taste=适量/按口味"
        )
    )
    group: str | None = Field(None, description="原文的分组小标题或冒号行，如「辅料」「香料」「葱油酱汁基础量」「每份面条」；无则 null")
    optional: bool = Field(False, description="原文标注可选/可不放时为 true")
    note: str | None = Field(None, description="处理方式或补充说明，如「切段」「约 2 个」；无则 null")

    @model_validator(mode="after")
    def _normalize(self) -> Ingredient:
        # 确定性规则在这里兜底，比改 Prompt 更可靠，且读取旧缓存时同样生效
        if self.unit:
            unit = _UNIT_ALIASES.get(self.unit.strip(), self.unit.strip())
            if unit in _UNIT_FACTORS:
                unit, factor = _UNIT_FACTORS[unit]
                self.amount = self.amount * factor if self.amount is not None else None
                self.amount_max = self.amount_max * factor if self.amount_max is not None else None
            self.unit = unit
        if self.amount is None:
            self.amount_max = None
            self.scaling = "to_taste"
        elif self.amount_max is not None and self.amount_max < self.amount:
            self.amount, self.amount_max = self.amount_max, self.amount
        return self


class RecipeFacts(BaseModel):
    serving_size_people: float | None = Field(
        None, description="原文中「一份够几个人吃」；按每人给用量时填 1；原文未说明时为 null"
    )
    ingredients: list[Ingredient] = Field(
        description="「计算」一节的全部原料及用量，加上「必备原料」中出现但「计算」未给用量的食材（scaling=to_taste）"
    )
    tools: list[str] = Field(description="需要的特殊厨具，如空气炸锅、烤箱、高压锅；常规锅碗刀不算")
    main_ingredients: list[str] = Field(description="决定这道菜的 1-4 个主料（规范化名），用于按现有食材推荐")
    flavors: list[Flavor] = Field(description="口味标签（只能用枚举值）")
    methods: list[Method] = Field(description="主要烹饪方式（只能用枚举值）")
    is_vegetarian: bool = Field(description="不含任何肉类、水产（蛋奶可以）时为 true")
    total_time_minutes: int | None = Field(None, description="预估总耗时（分钟），优先采用原文给出的时间")

    @field_validator("flavors", mode="before")
    @classmethod
    def _norm_flavors(cls, v):
        return _normalize_tags(v, get_args(Flavor))

    @field_validator("methods", mode="before")
    @classmethod
    def _norm_methods(cls, v):
        return _normalize_tags(v, get_args(Method))

    @model_validator(mode="after")
    def _check_non_empty(self) -> RecipeFacts:
        if not self.ingredients:
            raise ValueError("ingredients 不能为空")
        return self


class Recipe(ParsedRecipe):
    facts: RecipeFacts | None = Field(None, description="LLM 抽取结果；抽取失败时为 None")
    facts_model: str | None = Field(None, description="生成 facts 的实际模型名，便于追溯")
    allergens: list[Allergen] = Field(
        default_factory=list, description="规则推导的过敏原（见 ingest/allergens.py），可能漏标，需以原料表为准"
    )
