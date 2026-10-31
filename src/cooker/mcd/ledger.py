"""写操作台账（SQLite）：保证「一次确认最多执行一次」，结果未知时冻结。

状态机：

    begin ──→ IN_FLIGHT ──settle──→ SUCCESS / FAILED / UNKNOWN
                                                  │
                     人工核实后（本文件的命令行）resolve ──→ SUCCESS / FAILED

- 主键是 tool_call_id：LangGraph 从中断恢复时工具会从头重跑，进程崩溃后重放也会再跑一次，
  同一个 tool_call_id 第二次进来只返回已记录的结果，不会再发请求（幂等）。
- 认领在 BEGIN IMMEDIATE 事务内完成（检查 + 插入是原子的），跨进程、重启、删除聊天记录都有效。
- 账户下有 IN_FLIGHT / UNKNOWN 记录时拒绝任何新的写操作：结果不明还继续下单 / 抽奖可能重复扣费。
  解冻只能由人在核实后执行命令行，不提供给模型调用。

    .venv/Scripts/python.exe -m cooker.mcd.ledger list
    .venv/Scripts/python.exe -m cooker.mcd.ledger resolve <id> FAILED --note "App 中无此订单"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from cooker.settings import get_settings

DRAW_INTERVAL = 1.0  # draw-lottery 服务端频控：1 秒内不可重复


class Frozen(RuntimeError):
    """账户存在未结清的写操作，或触发本地频控；消息可直接展示给用户。"""


class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = path or get_settings().data_dir / "mcd-ledger.sqlite3"  # 旧版工作台用过 mcd-actions.sqlite3，表结构不同
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL,
                fingerprint TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                created REAL NOT NULL, updated REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS account_status ON actions(account, status)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)  # 事务由下面显式控制
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def begin(self, id: str, account: str, tool: str, args: dict, fingerprint: str) -> dict | None:
        """认领一次写操作。返回 None 表示认领成功、可以发请求；返回 dict 表示这次调用已执行过。"""
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")  # 立即拿写锁：两个进程不会同时通过下面的检查
            try:
                row = db.execute("SELECT * FROM actions WHERE id=?", (id,)).fetchone()
                if row:
                    db.execute("COMMIT")
                    return {**dict(row), "result": json.loads(row["result"]) if row["result"] else None}
                pending = db.execute("SELECT tool FROM actions WHERE account=? AND status IN ('IN_FLIGHT','UNKNOWN')",
                                     (account,)).fetchone()
                if pending:
                    raise Frozen(f"账户有一笔结果未知的操作（{pending['tool']}），已冻结所有写操作。"
                                 "请先在麦当劳 App 核实，再由本人用台账命令解冻。")
                if tool == "draw-lottery" and db.execute(
                        "SELECT 1 FROM actions WHERE account=? AND tool='draw-lottery' AND updated>?",
                        (account, now - DRAW_INTERVAL)).fetchone():
                    raise Frozen("抽奖间隔至少 1 秒，请稍后重新确认")
                db.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?)",
                           (id, account, tool, json.dumps(args, ensure_ascii=False), fingerprint,
                            "IN_FLIGHT", None, now, now))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return None

    def get(self, id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "result": json.loads(row["result"]) if row["result"] else None}

    def pending(self, account: str) -> str | None:
        """账户下未结清（IN_FLIGHT / UNKNOWN）的写操作工具名；没有返回 None。"""
        with self._db() as db:
            row = db.execute("SELECT tool FROM actions WHERE account=? AND status IN ('IN_FLIGHT','UNKNOWN')",
                             (account,)).fetchone()
        return row["tool"] if row else None

    def settle(self, id: str, status: str, result: object) -> None:
        assert status in ("SUCCESS", "FAILED", "UNKNOWN")
        with self._db() as db:
            db.execute("UPDATE actions SET status=?, result=?, updated=? WHERE id=? AND status='IN_FLIGHT'",
                       (status, json.dumps(result, ensure_ascii=False), time.time(), id))

    def resolve(self, id: str, status: str, note: str) -> bool:
        """人工核实后结清一笔 IN_FLIGHT / UNKNOWN 记录。"""
        assert status in ("SUCCESS", "FAILED")
        with self._db() as db:
            return db.execute(
                "UPDATE actions SET status=?, result=?, updated=? WHERE id=? AND status IN ('IN_FLIGHT','UNKNOWN')",
                (status, json.dumps({"人工核实": note}, ensure_ascii=False), time.time(), id)).rowcount == 1

    def list(self, limit: int = 20) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT id, tool, status, created, result FROM actions "
                              "ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="麦当劳写操作台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="最近 20 条")
    r = sub.add_parser("resolve", help="核实后结清结果未知的记录")
    r.add_argument("id")
    r.add_argument("status", choices=["SUCCESS", "FAILED"])
    r.add_argument("--note", required=True, help="核实依据（不要写手机号、地址等隐私）")
    args = ap.parse_args()
    ledger = Ledger()
    if args.cmd == "list":
        for row in ledger.list():
            when = time.strftime("%m-%d %H:%M:%S", time.localtime(row["created"]))
            print(f"{when}  {row['status']:<9} {row['tool']:<22} {row['id']}")
    else:
        print("已结清" if ledger.resolve(args.id, args.status, args.note) else "没有找到待结清的该记录")


if __name__ == "__main__":
    main()
