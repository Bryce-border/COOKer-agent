from pathlib import Path
from openai import OpenAI
env = dict(l.split("=", 1) for l in Path(".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
client = OpenAI(api_key=env["DASHSCOPE_API_KEY"], base_url=env["DASHSCOPE_BASE_URL"])
prompt = "只回复两个字：你好"  # 只回复两个字：你好（转义，排除源文件编码因素）
print("prompt codepoints ok:", prompt == "只回复两个字：你好")
r = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": prompt}], max_tokens=50)
c = r.choices[0].message.content
print([hex(ord(ch)) for ch in c])
r = client.chat.completions.create(model="qwen-plus", messages=[{"role": "user", "content": "Reply with the Chinese word for hello and its pinyin."}], max_tokens=50)
print([hex(ord(ch)) for ch in r.choices[0].message.content][:20])
