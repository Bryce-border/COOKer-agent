"""确认 DeepSeek（经百炼托管）是否默认开启思考模式。"""
from pathlib import Path
from openai import OpenAI

env = dict(l.split("=", 1) for l in Path(".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
client = OpenAI(api_key=env["DASHSCOPE_API_KEY"], base_url=env["DASHSCOPE_BASE_URL"])
for model in ["qwen-plus", "deepseek-v4.1-flash"]:
    for extra in [None, {"enable_thinking": False}]:
        r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "只回复两个字：你好"}],
                                           max_tokens=200, extra_body=extra)
        m = r.choices[0].message
        rc = getattr(m, "reasoning_content", None) or (m.model_extra or {}).get("reasoning_content")
        print(f"{model:22} extra={extra}: content={m.content!r} reasoning_len={len(rc or '')} usage={r.usage.completion_tokens}")
