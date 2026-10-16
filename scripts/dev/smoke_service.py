import time
from cooker.recipes.store import RecipeFilter
from cooker.service import get_service

t0 = time.perf_counter(); svc = get_service(); print(f"load {time.perf_counter()-t0:.2f}s")
for q, flt in [("红烧肉怎么做", None), ("下饭的辣菜", RecipeFilter(max_difficulty=3)), ("夏天喝的清爽饮品", None),
               ("适合新手的快手早餐", RecipeFilter(categories=["早餐"])), ("用啤酒炖的鸭子", None), ("不辣的素菜", RecipeFilter(vegetarian=True))]:
    t0 = time.perf_counter(); rs = svc.search_recipes(q, flt, 5)
    print(f"[{time.perf_counter()-t0:.2f}s] {q}: " + " | ".join(f"{r.recipe.name}({r.score:.2f})" for r in rs))
for q in ["油温怎么判断", "肉怎么去腥"]:
    print(q, [t.title for t in svc.search_tips(q)])
print("have 土豆 鸡蛋 番茄:")
for m in svc.recommend(["土豆", "鸡蛋", "西红柿"]):
    print("  ", m.name, "matched", m.matched, "missing_main", m.missing_main, "other", m.missing_other[:4])
s = svc.scale("红烧茄子", 5); print(s.factor_note); print("  ", [i.display() for i in s.items][:6]); print("  ", s.warnings)
sl = svc.shopping([("番茄炒蛋", 3), ("红烧茄子", 3), ("尖椒炒牛肉", 3)])
print("groceries:", [(l.name, l.amount) for l in sl.groceries]); print("pantry:", [(l.name, l.amount) for l in sl.pantry])
try: svc.get_recipe("红烧")
except Exception as e: print("find:", e)
print(svc.get_recipe("可乐鸡翅的做法").summary)
