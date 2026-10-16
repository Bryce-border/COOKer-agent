from pathlib import Path
from openai import OpenAI
env = dict(l.split("=", 1) for l in Path(".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
c = OpenAI(api_key=env["DASHSCOPE_API_KEY"], base_url=env["DASHSCOPE_BASE_URL"])
for n in [20, 21]:
    try:
        c.embeddings.create(model="qwen3.7-text-embedding", input=["x"] * n); print(n, "ok")
    except Exception as e:
        print(n, "fail", str(e)[150:260])
