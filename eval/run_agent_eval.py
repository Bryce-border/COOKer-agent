"""Agent 场景评测：真实模型 + 真实检索，检查工具选择与关键约束。

判定只看可客观验证的行为（调用了哪些工具、参数、档案是否写入），不评价回答文笔。
用法：PYTHONUTF8=1 .venv/Scripts/python eval/run_agent_eval.py [--provider qwen|deepseek] [--show]
"""

import argparse
import json
import re
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from cooker.agent.graph import build_graph
from cooker.agent.profile import UserProfile, load_profile, save_profile
from cooker.agent.tools import Context
from cooker.recipes.ingredients import dish_key
from cooker.recipes.store import get_store

_CITE_RE = re.compile(r"【([^】]{1,20})】")


def grounding_errors(answer: str, tool_text: str) -> list[str]:
    """检查回答中每个【菜名】：必须是菜谱库里真实存在的菜，且出现在本次对话的工具结果中。"""
    store, errs = get_store(), []
    names = {dish_key(r.name): r.name for r in store.recipes}
    for cited in dict.fromkeys(_CITE_RE.findall(answer)):
        real = names.get(dish_key(cited))
        if real is None:
            errs.append(f"【{cited}】不在菜谱库")
        elif real not in tool_text:
            errs.append(f"【{cited}】未出现在工具结果中")
    return errs

# 每个场景：多轮用户输入、预置档案、检查函数（接收 calls / store / 最终回答，返回失败原因或 None）
SCENARIOS = [
    {"id": "have_ingredients", "turns": ["我家冰箱里有土豆、鸡蛋和番茄，今晚能做点什么？"],
     "check": lambda c, s, a: None if "recommend_by_ingredients" in c else "应调用 recommend_by_ingredients"},
    {"id": "how_to_cook", "turns": ["可乐鸡翅怎么做？"],
     "check": lambda c, s, a: None if "get_recipe" in c else "应调用 get_recipe"},
    {"id": "scale", "turns": ["红烧茄子我要做 6 个人的量，各种材料要准备多少？"],
     "check": lambda c, s, a: None if any(n == "scale_recipe" and x.get("people") == 6 for n, x in c.items_all)
     else "应调用 scale_recipe(people=6)"},
    {"id": "tip", "turns": ["油温怎么判断？七成热是多少度？"],
     "check": lambda c, s, a: None if "search_cooking_tips" in c else "应调用 search_cooking_tips"},
    {"id": "remember_allergy", "turns": ["我对花生过敏，以后推荐菜都避开花生。"],
     "check": lambda c, s, a: None if "花生" in load_profile(s, "local").allergies else "档案中未记录花生过敏"},
    {"id": "dislike_ingredient", "turns": ["我不吃香菜，记一下"],
     "check": lambda c, s, a: None if "香菜" in load_profile(s, "local").dislikes else "香菜应记为忌口"},
    {"id": "shopping", "turns": ["帮我列个购物清单：西红柿炒鸡蛋和可乐鸡翅，都是 3 个人吃"],
     "check": lambda c, s, a: None if "make_shopping_list" in c else "应调用 make_shopping_list"},
    {"id": "what_to_eat", "turns": ["今天不知道吃什么，随便推荐一个简单的素菜吧"],
     "check": lambda c, s, a: None if ({"browse_recipes", "search_recipes"} & set(c)) else "应筛选或搜索菜谱"},
    {"id": "profile_enforced", "profile": UserProfile(allergies=["蛋"]),
     "turns": ["推荐几个早餐"],
     "check": lambda c, s, a: _no_egg_recommended(c)},
    {"id": "conflict_warning", "profile": UserProfile(allergies=["蛋"]),
     "turns": ["西红柿炒鸡蛋怎么做"],
     "check": lambda c, s, a: None if (c and "过敏" in a) else "应先查询菜谱，并提醒过敏冲突"},
    {"id": "not_in_library", "turns": ["佛跳墙怎么做？"],
     "check": lambda c, s, a: None if (("没有" in a or "未收录" in a or "不在" in a) and c) else "应先查询，再如实说明库中没有"},
    {"id": "multi_turn_followup", "turns": ["推荐一道简单的荤菜", "这道菜做 4 个人的量要多少料？"],
     # 第一轮若推荐了多道菜，「这道菜」有歧义，反问属于正确行为（Prompt 规则 7）
     "check": lambda c, s, a: None if ("scale_recipe" in c or "哪一道" in a or "哪道" in a)
     else "第二轮应调用 scale_recipe，或在指代不明时反问"},
]


def _no_egg_recommended(calls) -> str | None:
    for name, _args, result in calls.results:
        for r in (result or {}).get("results", []):
            recipe = r.get("recipe", r)
            if "蛋" in recipe.get("allergens", []):
                return f"推荐结果含蛋：{recipe.get('name')}"
    return None if calls else "应调用检索工具"


class Calls(dict):
    """{工具名: 最后一次参数}，另保留全部调用与结果。"""

    def __init__(self):
        super().__init__()
        self.items_all, self.results = [], []


def run(provider: str | None, show: bool, repeat: int = 1, only: list[str] | None = None) -> dict:
    rows = []
    for sc in [s for s in SCENARIOS if not only or s["id"] in only for _ in range(repeat)]:
        store = InMemoryStore()
        if sc.get("profile"):
            save_profile(store, "local", sc["profile"])
        graph = build_graph(checkpointer=InMemorySaver(), store=store)
        cfg = {"configurable": {"thread_id": f"{sc['id']}-{len(rows)}"}, "recursion_limit": 30}
        calls, t0, error = Calls(), time.perf_counter(), None
        answer = ""
        try:
            for turn in sc["turns"]:
                state = graph.invoke({"messages": [HumanMessage(turn)]}, cfg, context=Context(provider=provider))
            pending = {}
            for m in state["messages"]:
                if isinstance(m, AIMessage):
                    for tc in m.tool_calls:
                        calls[tc["name"]] = tc["args"]
                        calls.items_all.append((tc["name"], tc["args"]))
                        pending[tc["id"]] = (tc["name"], tc["args"])
                elif m.type == "tool" and m.tool_call_id in pending:
                    try:
                        res = json.loads(m.content)
                    except json.JSONDecodeError:
                        res = None
                    calls.results.append((*pending[m.tool_call_id], res))
            answer = state["messages"][-1].content
            tool_text = "\n".join(m.content for m in state["messages"] if m.type == "tool")
            error = sc["check"](calls, store, answer)
            ground = grounding_errors(answer, tool_text)
            if ground:
                error = "; ".join(filter(None, [error, *ground]))
            cites = len(set(_CITE_RE.findall(answer)))
            fixes = sum(str(m.id or "").startswith("do-not-render-verify-") for m in state["messages"])
        except Exception as e:
            error = f"异常 {type(e).__name__}: {str(e)[:150]}"
        rows.append({"id": sc["id"], "ok": error is None, "reason": error, "cites": locals().get("cites", 0), "fixes": locals().get("fixes", 0),
                     "tools": [n for n, _ in calls.items_all],
                     "seconds": round(time.perf_counter() - t0, 1)})
        print(f"{'✅' if error is None else '❌'} {sc['id']:22} {rows[-1]['seconds']:>5}s fixes={rows[-1]['fixes']} cites={rows[-1]['cites']} tools={rows[-1]['tools']}"
              + (f"  ← {error}" if error else ""))
        if show:
            print("   " + answer[:300].replace("\n", "\n   "))
    passed = sum(r["ok"] for r in rows)
    flaky = sorted({r["id"] for r in rows if not r["ok"]})
    fixed = sum(r["fixes"] > 0 for r in rows)
    print(f"[{provider or 'default'}] 通过 {passed}/{len(rows)}（{repeat} 次/场景），"
          f"平均 {sum(r['seconds'] for r in rows) / len(rows):.1f}s/次；verify 介入 {fixed} 次；出现失败的场景：{flaky or '无'}")
    return {"provider": provider, "passed": passed, "total": len(rows), "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", action="append", help="可多次指定；默认 qwen 和 deepseek 都跑")
    ap.add_argument("--show", action="store_true", help="打印回答")
    ap.add_argument("--repeat", type=int, default=1, help="每个场景重复次数")
    ap.add_argument("--only", nargs="*", help="只跑指定场景")
    args = ap.parse_args()
    reports = [run(p, args.show, args.repeat, args.only) for p in (args.provider or ["qwen", "deepseek"])]
    if args.only:
        return  # 只跑部分场景时不覆盖完整报告
    Path("data/eval_agent.json").write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
