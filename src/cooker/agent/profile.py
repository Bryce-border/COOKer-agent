"""长期记忆：用户档案（本人 + 家人，按人分别记录）。

保存在 LangGraph Store（键值存储，按 namespace 隔离）中，跨会话保留。
偏好是少量结构化字段，直接按键读写即可，不需要向量化的「语义记忆」。

按人记录的原因：「我妹妹不吃芹菜」不能记成用户本人忌口，否则给用户自己推荐菜时也会排除芹菜。
本人的偏好沿用顶层字段（兼容已保存的旧档案），家人记在 members，键是称呼（妹妹、妈妈、儿子）。
"""

from __future__ import annotations

import re
from typing import Literal

from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field

from cooker.ingest.schema import Allergen

PROFILE_KEY = "profile"
SELF = "本人"
EVERYONE = ("全家", "所有人", "大家", "一家人")
_SELF_ALIASES = {"本人", "我", "自己", "我自己", "用户", "me", "self"}


def namespace(user_id: str) -> tuple[str, ...]:
    return ("users", user_id)


def normalize_person(name: str | None) -> str:
    """「我妹妹」「我的妹妹」→「妹妹」；「我」「自己」→「本人」。"""
    name = (name or "").strip()
    if not name or name.lower() in _SELF_ALIASES:
        return SELF
    return re.sub(r"^我(的)?", "", name).strip() or SELF


class PersonPrefs(BaseModel):
    """一个人的饮食限制：检索时按「这次谁吃」合并。"""

    allergies: list[Allergen] = Field(default_factory=list, description="过敏原：检索与推荐时强制排除")
    dislikes: list[str] = Field(default_factory=list, description="忌口食材：检索与推荐时强制排除")
    vegetarian: bool = False

    def is_empty(self) -> bool:
        return self == PersonPrefs()

    def describe(self) -> str:
        parts = []
        if self.allergies:
            parts.append(f"过敏：{'、'.join(self.allergies)}")
        if self.dislikes:
            parts.append(f"忌口：{'、'.join(self.dislikes)}")
        if self.vegetarian:
            parts.append("素食")
        return "；".join(parts)


class UserProfile(PersonPrefs):
    """顶层的 allergies / dislikes / vegetarian 是用户本人的；家里的设定（人数、厨具、厨艺）属于用户。"""

    household_size: int | None = Field(None, ge=1, le=50, description="常用就餐人数，换算用量时的默认值")
    equipment: list[str] = Field(default_factory=list, description="家里的特殊厨具，如烤箱、空气炸锅")
    skill: Literal["新手", "有经验", "熟练"] | None = None
    members: dict[str, PersonPrefs] = Field(default_factory=dict, description="家人 / 朋友的偏好：称呼 → 偏好")

    def is_empty(self) -> bool:
        return self == UserProfile()

    def person(self, name: str) -> PersonPrefs:
        """某人的偏好（只读副本）；档案里没有此人时返回空偏好。"""
        if name == SELF:
            return PersonPrefs(allergies=self.allergies, dislikes=self.dislikes, vegetarian=self.vegetarian)
        return self.members.get(name, PersonPrefs())

    def set_person(self, name: str, prefs: PersonPrefs) -> None:
        if name == SELF:
            self.allergies, self.dislikes, self.vegetarian = prefs.allergies, prefs.dislikes, prefs.vegetarian
        elif prefs.is_empty():
            self.members.pop(name, None)  # 没有任何偏好了就不再保留此人
        else:
            self.members[name] = prefs

    def everyone(self) -> list[str]:
        return [SELF, *self.members]

    def resolve_diners(self, people: list[str] | None) -> tuple[list[str], list[str]]:
        """「这次谁吃」→（档案里的人，档案里没有的称呼）。不填只算本人；「全家」等于档案里所有人。"""
        if not people:
            return [SELF], []
        names: list[str] = []
        for p in people:
            names.extend(self.everyone() if p.strip() in EVERYONE else [normalize_person(p)])
        names = list(dict.fromkeys(names))
        unknown = [n for n in names if n != SELF and n not in self.members]
        return names, unknown

    def describe(self) -> str:
        if self.is_empty():
            return "（尚未记录任何偏好）"
        parts = []
        if own := PersonPrefs.describe(self):
            parts.append(f"本人 {own}")
        if self.members:
            parts.append("家人：" + "，".join(f"{n}（{p.describe()}）" for n, p in self.members.items()))
        if self.household_size:
            parts.append(f"常用人数：{self.household_size} 人")
        if self.equipment:
            parts.append(f"厨具：{'、'.join(self.equipment)}")
        if self.skill:
            parts.append(f"厨艺：{self.skill}")
        return "；".join(parts)


def load_profile(store: BaseStore | None, user_id: str) -> UserProfile:
    if store is None:
        return UserProfile()
    item = store.get(namespace(user_id), PROFILE_KEY)
    return UserProfile.model_validate(item.value) if item else UserProfile()


def save_profile(store: BaseStore, user_id: str, profile: UserProfile) -> None:
    store.put(namespace(user_id), PROFILE_KEY, profile.model_dump(mode="json"))
