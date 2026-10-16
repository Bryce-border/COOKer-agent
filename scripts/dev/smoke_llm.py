"""冒烟测试：经工厂分别调用 Qwen / DeepSeek，并验证结构化输出。"""
from pydantic import BaseModel
from cooker.llm import get_chat_model

class Answer(BaseModel):
    dish: str
    difficulty: int

for provider in ["qwen", "deepseek"]:
    m = get_chat_model("extract", provider)
    r = m.invoke("用一句话说番茄炒蛋难不难。")
    print(provider, type(m).__name__, m.model_name, "->", r.content[:40], "| model:", r.response_metadata.get("model_name"))
    for method in ["function_calling", "json_mode", "json_schema"]:
        try:
            out = m.with_structured_output(Answer, method=method).invoke("番茄炒蛋，难度 1 到 5 打几分？以 JSON 返回 dish 和 difficulty。")
            print(f"  structured[{method}] ok:", out)
        except Exception as e:
            print(f"  structured[{method}] FAIL:", type(e).__name__, str(e)[:150])
