import pytest


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["db"], bool)


def test_health_db_connected(client, test_db_configured):
    if not test_db_configured:
        pytest.skip("未配置 DOCBASE_TEST_DATABASE_URL")
    body = client.get("/api/health").json()
    assert body["db"] is True
    assert body["database"].endswith("_test")  # 测试一定连的是测试库


def test_sample_mdl_fixture(sample_mdl_path):
    assert sample_mdl_path.suffix == ".xlsx"
