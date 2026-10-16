"""长期记忆：用户档案。

保存在 LangGraph Store（键值存储，按 namespace 隔离）中，跨会话保留。
偏好是少量结构化字段，直接按键读写即可，不需要向量化的「语义记忆」。
"""

from __future__ import annotations

from typing import Literal

from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field

from cooker.ingest.schema import Allergen

PROFILE_KEY = "profile"


def namespace(user_id: str) -> tuple[str, ...]:
    return ("users", user_id)


class UserProfile(BaseModel):
    allergies: list[Allergen] = Field(default_factory=list, description="过敏原：检索与推荐时强制排除")
    dislikes: list[str] = Field(default_factory=list, description="忌口食材：检索与推荐时强制排除")
    vegetarian: bool = False
    household_size: int | None = Field(None, ge=1, le=50, description="常用就餐人数，换算用量时的默认值")
    equipment: list[str] = Field(default_factory=list, description="家里的特殊厨具，如烤箱、空气炸锅")
    skill: Literal["新手", "有经验", "熟练"] | None = None

    def is_empty(self) -> bool:
        return self == UserProfile()

    def describe(self) -> str:
        if self.is_empty():
            return "（尚未记录任何偏好）"
        parts = []
        if self.allergies:
            parts.append(f"过敏：{'、'.join(self.allergies)}")
        if self.dislikes:
            parts.append(f"忌口：{'、'.join(self.dislikes)}")
        if self.vegetarian:
            parts.append("素食")
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
