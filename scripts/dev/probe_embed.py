"""探测百炼向量 / 重排接口：维度、批量上限、重排端点格式。"""
import json
from pathlib import Path
import httpx
from openai import OpenAI

env = dict(l.split("=", 1) for l in Path(".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
client = OpenAI(api_key=env["DASHSCOPE_API_KEY"], base_url=env["DASHSCOPE_BASE_URL"])
for model in ["qwen3.7-text-embedding", "qwen3.7-text-embedding-flash", "text-embedding-v4"]:
    try:
        r = client.embeddings.create(model=model, input=["番茄炒蛋怎么做", "红烧肉"])
        print(f"[embed ok] {model}: dim={len(r.data[0].embedding)} usage={r.usage}")
    except Exception as e:
        print(f"[embed fail] {model}: {type(e).__name__} {str(e)[:160]}")
m = "qwen3.7-text-embedding"
for dims in [512, 1024]:
    try:
        r = client.embeddings.create(model=m, input=["测试"], dimensions=dims)
        print(f"dimensions={dims} -> {len(r.data[0].embedding)}")
    except Exception as e:
        print(f"dimensions={dims} fail: {str(e)[:120]}")
for n in [10, 11, 25, 64]:
    try:
        client.embeddings.create(model=m, input=[f"文本{i}" for i in range(n)])
        print(f"batch {n} ok")
    except Exception as e:
        print(f"batch {n} fail: {str(e)[:150]}")

# 重排：先试 OpenAI 兼容路径，再试 DashScope 原生路径
host = env["DASHSCOPE_BASE_URL"].split("/compatible-mode")[0]
docs = ["番茄炒蛋：鸡蛋和番茄同炒", "红烧肉：五花肉慢炖", "如何焯水"]
H = {"Authorization": f"Bearer {env['DASHSCOPE_API_KEY']}", "Content-Type": "application/json"}
tries = [
    (f"{host}/compatible-api/v1/reranks", {"model": "qwen3.7-text-rerank", "query": "鸡蛋做什么菜", "documents": docs, "top_n": 2}),
    (f"{host}/compatible-mode/v1/rerank", {"model": "qwen3.7-text-rerank", "query": "鸡蛋做什么菜", "documents": docs, "top_n": 2}),
    (f"{host}/api/v1/services/rerank/text-rerank/text-rerank", {"model": "qwen3.7-text-rerank", "input": {"query": "鸡蛋做什么菜", "documents": docs}, "parameters": {"top_n": 2, "return_documents": False}}),
]
for url, body in tries:
    try:
        r = httpx.post(url, headers=H, json=body, timeout=30)
        print(f"[rerank {r.status_code}] {url.replace(host, '<host>')}: {r.text[:260]}")
    except Exception as e:
        print(f"[rerank err] {url.replace(host, '<host>')}: {e}")
