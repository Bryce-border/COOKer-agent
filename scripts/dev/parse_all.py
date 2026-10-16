"""对全部菜谱跑规则解析，统计失败与可疑结果。"""
from collections import Counter
from cooker.ingest.parser import iter_recipe_files, iter_tip_files, parse_recipe, parse_tip, RecipeFormatError
from cooker.settings import get_settings

root = get_settings().howtocook_dir
ok, fail = [], []
for p in iter_recipe_files(root):
    try:
        ok.append(parse_recipe(p, root))
    except RecipeFormatError as e:
        fail.append((p.relative_to(root).as_posix(), str(e)))
tips = [parse_tip(p, root) for p in iter_tip_files(root)]
print(f"recipes ok={len(ok)} fail={len(fail)} tips={len(tips)}")
for f in fail: print("  FAIL", f)
print("by category:", Counter(r.category_name for r in ok))
print("difficulty:", sorted(Counter(r.difficulty for r in ok).items()))
print("no steps:", [r.id for r in ok if not r.steps])
print("no ingredients:", [r.id for r in ok if not r.ingredients_raw])
print("no images:", sum(1 for r in ok if not r.images))
print("empty calc:", [r.id for r in ok if not r.calculation_raw])
step_counts = [sum(len(g.steps) for g in r.steps) for r in ok]
print("steps per recipe min/avg/max:", min(step_counts), sum(step_counts)/len(step_counts), max(step_counts))
print("few steps (<2):", [r.id for r, c in zip(ok, step_counts) if c < 2])
r = next(r for r in ok if r.name == "乡村啤酒鸭")
for g in r.steps: print(g.title, g.steps[:5])
print(tips[0].id, tips[0].title)
