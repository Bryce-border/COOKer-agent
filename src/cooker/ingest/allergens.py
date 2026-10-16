"""过敏原推导：基于关键词词典的确定性规则，不依赖 LLM 判断。

过敏原属于安全相关信息，LLM 自由判断时实测会大面积误标（如给冬瓜茶标「大豆」）。
规则方式可审计、可复现，代价是词典覆盖不到的食材会漏标——因此上层回答
必须提示用户以原料表为准。
"""

from __future__ import annotations

from collections.abc import Iterable

# 过敏原 → 命中关键词（子串匹配）。新增食材时在这里补充即可。
ALLERGEN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "花生": ("花生",),
    "坚果": ("核桃", "杏仁", "腰果", "榛子", "开心果", "碧根果", "夏威夷果", "松子", "栗子", "板栗"),
    "乳制品": ("牛奶", "奶油", "黄油", "芝士", "奶酪", "炼乳", "淡奶", "奶粉", "酸奶", "乳酪", "马斯卡彭", "百利甜"),
    "蛋": ("鸡蛋", "鸭蛋", "鹌鹑蛋", "皮蛋", "咸蛋", "蛋黄", "蛋清", "蛋白", "全蛋"),
    "海鲜": ("虾", "蟹", "贝", "蛤", "蚬", "蛏", "鱿鱼", "墨鱼", "章鱼", "生蚝", "牡蛎", "扇贝", "海参", "鲍鱼", "海带", "紫菜"),
    "鱼": ("鱼",),
    "大豆": ("大豆", "黄豆", "豆腐", "豆浆", "腐竹", "豆皮", "千张", "酱油", "生抽", "老抽", "豆瓣", "黄豆酱", "味噌", "豆豉", "腐乳", "纳豆", "蚝油"),
    "麸质": ("面粉", "面条", "挂面", "拉面", "意面", "面包", "馒头", "饺子皮", "馄饨皮", "春卷皮", "面筋", "烤麸", "啤酒", "酱油", "生抽", "老抽", "小麦", "燕麦", "大麦", "吐司", "饼干", "淀粉肠"),
    "芝麻": ("芝麻", "香油", "麻油", "麻酱"),
}

# 含关键词但并不是该过敏原的食材（子串匹配的误伤）
_EXCLUDES: dict[str, tuple[str, ...]] = {
    "鱼": ("鱼露", "鱼香", "鱿鱼", "墨鱼", "章鱼"),  # 鱼香是调味风格；鱿鱼等归入海鲜
    "蛋": ("蛋白质",),
    "麸质": ("玉米淀粉", "土豆淀粉", "红薯淀粉", "绿豆面"),
}


def derive_allergens(ingredient_texts: Iterable[str]) -> list[str]:
    """输入原料文本（规范化名或原文均可），返回按词典顺序排列的过敏原列表。"""
    texts = [t for t in ingredient_texts if t]
    found: list[str] = []
    for allergen, keywords in ALLERGEN_KEYWORDS.items():
        excludes = _EXCLUDES.get(allergen, ())
        for text in texts:
            cleaned = text
            for ex in excludes:
                cleaned = cleaned.replace(ex, "")
            if any(k in cleaned for k in keywords):
                found.append(allergen)
                break
    return found
