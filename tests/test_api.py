"""前端用的自定义路由：菜谱详情与图片（重点测路径穿越防护）。"""

import pytest
from starlette.testclient import TestClient

from cooker.api import app, resolve_image


@pytest.fixture
def client(real_store):
    return TestClient(app)


def test_recipe_detail_by_synonym(client):
    r = client.get("/cooker/recipes", params={"name": "番茄炒蛋"})
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["name"] == "西红柿炒鸡蛋"
    assert all(i.startswith(("/cooker/images/dishes/", "http")) for i in data["images"])


def test_recipe_not_found_and_bad_input(client):
    r = client.get("/cooker/recipes", params={"name": "不存在的菜"})
    assert r.status_code == 404 and "suggestions" in r.json()
    assert client.get("/cooker/recipes").status_code == 400
    assert client.get("/cooker/recipes", params={"name": "x" * 51}).status_code == 400


def test_serves_real_image(client, real_store):
    name = next(r.name for r in real_store.recipes if any(not i.startswith("http") for i in r.images))
    img = client.get("/cooker/recipes", params={"name": name}).json()["images"]
    r = client.get(next(i for i in img if i.startswith("/cooker/")))
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")


@pytest.mark.parametrize("path", [
    "dishes/../../.env",
    "dishes/..%2F..%2F.env",
    "dishes/%2e%2e/%2e%2e/.env",
    "dishes/../README.md",
    "dishes/meat_dish/可乐鸡翅.md",      # 存在但不是图片
    "tips/learn/学习焯水.md",
    "dishes/不存在.jpg",
])
def test_image_traversal_blocked(client, path):
    assert client.get(f"/cooker/images/{path}").status_code == 404


def test_resolve_image_rejects_outside(tmp_path):
    root = tmp_path / "dishes"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"x")
    (tmp_path / "secret.jpg").write_bytes(b"x")
    assert resolve_image("a.jpg", root) == (root / "a.jpg").resolve()
    assert resolve_image("../secret.jpg", root) is None
    assert resolve_image(str(tmp_path / "secret.jpg"), root) is None  # 绝对路径
