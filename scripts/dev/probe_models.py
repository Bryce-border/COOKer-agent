"""探测百炼工作空间可用模型（只打印模型 ID，不打印密钥）。"""
import os, sys
from pathlib import Path
from openai import OpenAI

env = dict(l.split("=", 1) for l in Path(".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
client = OpenAI(api_key=env["DASHSCOPE_API_KEY"], base_url=env["DASHSCOPE_BASE_URL"])
try:
    ids = sorted(m.id for m in client.models.list().data)
    print(len(ids), "models")
    for i in ids:
        if any(k in i for k in ("qwen-plus", "qwen-flash", "qwen-max", "qwen3", "deepseek", "embedding", "rerank", "vl")):
            print(" ", i)
except Exception as e:
    print("models.list failed:", type(e).__name__, str(e)[:300])
for model in sys.argv[1:]:
    try:
        r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "只回复两个字：你好"}], max_tokens=10)
        print(f"[chat ok] {model}: {r.choices[0].message.content!r} usage={r.usage.total_tokens}")
    except Exception as e:
        print(f"[chat fail] {model}: {type(e).__name__} {str(e)[:200]}")
