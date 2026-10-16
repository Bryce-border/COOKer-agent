from pathlib import Path
import httpx
from openai import OpenAI
env = dict(l.split("=", 1) for l in Path(".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
c = OpenAI(api_key=env["DASHSCOPE_API_KEY"], base_url=env["DASHSCOPE_BASE_URL"], max_retries=0)
for m in ["qwen3.7-text-embedding", "qwen3.7-text-embedding-flash", "text-embedding-v4", "text-embedding-v3"]:
    try:
        r = c.embeddings.create(model=m, input=["测试"], dimensions=1024); print("embed ok", m, len(r.data[0].embedding))
    except Exception as e:
        print("embed FAIL", m, getattr(e, "status_code", ""), str(e)[60:160])
for m in ["qwen-flash", "qwen-plus"]:
    try:
        c.chat.completions.create(model=m, messages=[{"role": "user", "content": "hi"}], max_tokens=5); print("chat ok", m)
    except Exception as e:
        print("chat FAIL", m, str(e)[:120])
host = env["DASHSCOPE_BASE_URL"].split("/compatible-mode")[0]
r = httpx.post(f"{host}/compatible-api/v1/reranks", headers={"Authorization": f"Bearer {env['DASHSCOPE_API_KEY']}"},
               json={"model": "qwen3.7-text-rerank", "query": "a", "documents": ["a", "b"]}, timeout=20)
print("rerank", r.status_code, r.text[:120])
