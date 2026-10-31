"""挂载在 LangGraph Server 上的自定义 HTTP 路由（langgraph.json → http.app），供前端渲染菜谱卡片。

    GET /cooker/recipes?name=可乐鸡翅     菜谱详情（按菜名或 ID，与 Agent 的 get_recipe 同一套查找逻辑）
    GET /cooker/covers?names=甲&names=乙   批量取封面图（聊天回答下方的菜品图片条用，一条回答一次请求）
    GET /cooker/images/<仓库相对路径>      菜谱图片（只允许 HowToCook/dishes 下的图片文件）

只读、无副作用；用户档案不走这里，前端直接用 LangGraph Server 自带的 /store 接口。

路由刻意写成同步 def：处理过程有文件读取、首次加载菜谱库等阻塞操作，Starlette 会把同步路由放进线程池执行；
若写成 async def，这些阻塞调用会卡住事件循环，同一进程里正在流式输出的对话也会停顿
（langgraph dev 内置的 blockbuster 会直接报 BlockingError）。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from cooker.recipes.store import RecipeNotFound
from cooker.service import get_service
from cooker.settings import get_settings

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


def recipe_detail(request: Request) -> Response:
    name = request.query_params.get("name", "").strip()
    if not name or len(name) > 50:
        return JSONResponse({"error": "参数 name 必填且不超过 50 字"}, status_code=400)
    try:
        detail = get_service().get_recipe(name)
    except RecipeNotFound as e:
        return JSONResponse({"error": str(e), "suggestions": e.suggestions}, status_code=404)
    data = detail.model_dump(mode="json")
    # 本地图片改写为本服务的 URL；外链保持不变
    # 封面（成品图）放第一张，与聊天里的菜品图片条一致；其余保持原文顺序
    cover = pick_cover(detail.summary.name, detail.images)
    ordered = [cover, *(i for i in detail.images if i != cover)] if cover else []
    data["images"] = [_public_image(img) for img in ordered]
    return JSONResponse(data)


MAX_COVER_NAMES = 8
# 文件名提示「成品」的图优先；HowToCook 的图常按步骤命名（改刀、腌制、1、2…），第一张不一定是成品
_FINISHED_HINTS = ("成品", "做好", "完成", "成菜", "result", "final")


def pick_cover(name: str, images: list[str]) -> str | None:
    """从一道菜的图片里挑封面：文件名含成品类字样 > 与菜名相同或互相包含 > 摆盘 > 原顺序第一张。"""
    def score(img: str) -> int:
        stem = PurePosixPath(img).stem.lower()
        if any(h in stem for h in _FINISHED_HINTS):
            return 3
        if len(stem) >= 2 and (stem in name or name in stem):
            return 2
        return 1 if "摆盘" in stem else 0

    # max 遇到同分时保留先出现的那张，即原文中的顺序
    return max(images, key=score) if images else None


def _public_image(img: str) -> str:
    return img if img.startswith("http") else f"/cooker/images/{img}"


def recipe_covers(request: Request) -> Response:
    names = list(dict.fromkeys(n.strip() for n in request.query_params.getlist("names") if n.strip()))
    if not names or len(names) > MAX_COVER_NAMES or any(len(n) > 50 for n in names):
        return JSONResponse({"error": f"参数 names 必填，最多 {MAX_COVER_NAMES} 个，每个不超过 50 字"}, status_code=400)
    covers = []
    for n in names:
        try:
            detail = get_service().get_recipe(n)
        except RecipeNotFound:
            continue  # 回答里的【】偶尔不是库内菜（校验会兜底），这里静默跳过
        if cover := pick_cover(detail.summary.name, detail.images):
            covers.append({"query": n, "name": detail.summary.name, "image": _public_image(cover)})
    return JSONResponse({"covers": covers}, headers={"Cache-Control": "public, max-age=3600"})


def resolve_image(rel: str, root: Path) -> Path | None:
    """把用户提供的相对路径解析为 root 内的真实文件；越界、非图片、不存在都返回 None。

    先 resolve() 再比较：resolve 会展开 ..、符号链接，得到真实路径后再判断是否仍在 root 内，
    这样 ../../.env、%2e%2e 解码后的 ..、绝对路径、指向外部的符号链接都会被拒绝。
    """
    root = root.resolve()
    try:
        path = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if not path.is_relative_to(root) or path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
        return None
    return path


def recipe_image(request: Request) -> Response:
    rel = request.path_params["path"]
    if not rel.startswith("dishes/"):
        return Response(status_code=404)
    path = resolve_image(rel.removeprefix("dishes/"), get_settings().howtocook_dir / "dishes")
    if path is None:  # 越界与不存在统一返回 404，不泄露文件是否存在
        return Response(status_code=404)
    return FileResponse(path, media_type=IMAGE_TYPES[path.suffix.lower()],
                        headers={"Cache-Control": "public, max-age=86400"})


app = Starlette(routes=[
    Route("/cooker/recipes", recipe_detail),
    Route("/cooker/covers", recipe_covers),
    Route("/cooker/images/{path:path}", recipe_image),
])
