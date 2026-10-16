from pathlib import Path
from openai import OpenAI
env = dict(l.split("=", 1) for l in Path(".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
c = OpenAI(api_key=env["DASHSCOPE_API_KEY"], base_url=env["DASHSCOPE_BASE_URL"], max_retries=0)
tools = [{"type": "function", "function": {"name": "search", "description": "搜索菜谱", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}}]
for model in ["qwen-plus", "deepseek-v4.1-flash"]:
    for choice in ["auto", "required"]:
        try:
            r = c.chat.completions.create(model=model, messages=[{"role": "user", "content": "你好"}], tools=tools, tool_choice=choice,
                                          extra_body={"enable_thinking": False}, max_tokens=60)
            m = r.choices[0].message
            print(model, choice, "→ tool_calls:", [t.function.name for t in (m.tool_calls or [])], "content:", (m.content or "")[:20])
        except Exception as e:
            print(model, choice, "FAIL", str(e)[:150])
