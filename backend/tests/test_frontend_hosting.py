"""第 9 步：后端直接提供前端打包结果；接口地址不受影响。"""

import pytest

from app.config import get_settings


@pytest.fixture
def dist(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>docbase</html>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("no", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "frontend_dir", tmp_path)
    return tmp_path


def test_serves_files_and_falls_back_to_index(client, dist):
    assert client.get("/").text == "<html>docbase</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    # 前端路由：刷新任意页面都返回 index.html
    assert client.get("/projects/12/matrix").text == "<html>docbase</html>"
    # 不能读到 dist 外面的文件
    assert client.get("/../secret.txt").text == "<html>docbase</html>"
    assert client.get("/assets/..%2F..%2Fsecret.txt").text != "no"


def test_api_paths_are_not_swallowed(client, dist):
    assert client.get("/api/health").json()["ok"] is True
    resp = client.get("/api/no-such-endpoint")
    assert resp.status_code == 404 and resp.json() == {"detail": "Not Found"}
    # 接口地址带尾斜杠：仍按原来的方式重定向，不会被当成前端页面
    resp = client.get("/api/health/", follow_redirects=False)
    assert resp.status_code == 307
    assert client.get("/openapi.json").status_code == 200


def test_not_built(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "frontend_dir", tmp_path / "missing")
    resp = client.get("/")
    assert resp.status_code == 404 and resp.json()["detail"]["code"] == "frontend.not_built"
