"""终端聊天：cooker-chat

持久化到 data/agent.db：
- SqliteSaver（checkpointer）：按 thread_id 保存每一步的图状态 → 会话可续聊、可从中断处恢复
- SqliteStore（store）：跨会话的用户档案

命令：/new 新会话  /profile 查看档案  /provider qwen|deepseek 切换模型  /quit 退出
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import uuid

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.store.sqlite import SqliteStore
from langgraph.types import Command

from cooker.agent.graph import build_graph
from cooker.agent.profile import load_profile
from cooker.agent.tools import Context
from cooker.settings import get_settings

RECURSION_LIMIT = 30  # 单轮最多 30 步（约 14 次工具往返），防止模型陷入调用循环


def _short(obj, n: int = 120) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def _connect(db, autocommit: bool = False) -> sqlite3.Connection:
    """saver 与 store 各用一个连接，且事务模式不同（与两者自带的 from_conn_string 保持一致）：
    - SqliteSaver：Python 默认的隐式事务模式，自己调用 commit()
    - SqliteStore：autocommit（isolation_level=None），因为它自己执行显式 BEGIN；
      用默认模式会报 cannot start a transaction within a transaction
    check_same_thread=False：LangGraph 在工作线程中执行节点；WAL：允许一读一写并发。"""
    conn = sqlite3.connect(db, check_same_thread=False, isolation_level=None if autocommit else "")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def run_turn(graph, payload, config, context: Context, out=sys.stdout) -> list:
    """执行一轮并流式打印。返回本轮结束时挂起的 interrupt 列表（为空表示正常结束）。"""
    for mode, data in graph.stream(payload, config, context=context, stream_mode=["messages", "updates"]):
        if mode == "messages":
            chunk, meta = data
            if meta.get("langgraph_node") == "agent" and isinstance(chunk, AIMessageChunk) and chunk.content:
                out.write(chunk.content if isinstance(chunk.content, str) else "")
                out.flush()
        elif mode == "updates":
            for node, update in data.items():
                if node == "__interrupt__" or not update:
                    continue
                for msg in update.get("messages", []):
                    if isinstance(msg, AIMessage) and msg.tool_calls:
                        for tc in msg.tool_calls:
                            out.write(f"\n  ↳ 调用 {tc['name']}({_short(tc['args'])})\n")
                    elif isinstance(msg, ToolMessage):
                        out.write(f"  ↲ {msg.name}: {_short(msg.content, 80)}\n")
    out.write("\n")
    return list(graph.get_state(config).interrupts)


def main() -> None:
    ap = argparse.ArgumentParser(description="COOKer 终端聊天")
    ap.add_argument("--thread", help="续聊已有会话 ID")
    ap.add_argument("--provider", help="qwen / deepseek")
    ap.add_argument("--user", default="local")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    db = get_settings().data_dir / "agent.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    saver, store = SqliteSaver(_connect(db)), SqliteStore(_connect(db, autocommit=True))
    saver.setup()
    store.setup()
    graph = build_graph(checkpointer=saver, store=store)

    context = Context(user_id=args.user, provider=args.provider)
    thread = args.thread or uuid.uuid4().hex[:8]
    print(f"COOKer 已就绪（会话 {thread}，输入 /quit 退出，/new 新会话）")
    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/new":
            thread = uuid.uuid4().hex[:8]
            print(f"新会话 {thread}")
            continue
        if text == "/profile":
            print(load_profile(store, context.user_id).describe())
            continue
        if text.startswith("/provider"):
            context.provider = text.split(maxsplit=1)[1] if " " in text else None
            print(f"模型厂商：{context.provider or '默认'}")
            continue

        config = {"configurable": {"thread_id": thread}, "recursion_limit": RECURSION_LIMIT}
        payload = {"messages": [HumanMessage(text)]}
        try:
            print("COOKer> ", end="")
            interrupts = run_turn(graph, payload, config, context)
            while interrupts:  # 人在回路：把问题交给用户，用 Command(resume=…) 从断点继续
                answer = input(f"\n[需要确认] {interrupts[0].value.get('question')}（是/否）> ")
                interrupts = run_turn(graph, Command(resume=answer), config, context)
        except GraphRecursionError:
            print("\n（本轮步骤过多已中止，请换个说法或拆分问题）")
        except Exception as e:  # 网络 / 接口错误：本轮失败，会话保留
            print(f"\n（出错了：{type(e).__name__}: {str(e)[:200]}）")
    saver.conn.close()
    store.conn.close()


if __name__ == "__main__":
    main()
