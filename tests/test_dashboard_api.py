import importlib.util
from pathlib import Path

import pytest

from conftest import ROOT, FakeLlm, fenced, technique

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PASS = {"score": 9, "matches_prompt": True, "issues": []}


@pytest.fixture
def client():
    spec = importlib.util.spec_from_file_location("procgen_dashboard_api_test", ROOT / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/plugins/procgen-studio")
    return TestClient(app)


def test_pane_routes_list_detail_and_reroll(client, make_engine):
    engine, _ = make_engine(FakeLlm([fenced(technique("layered_scene.py"))], [PASS]))
    out = engine.generate("a moonlit cabin scene", aspect_ratio="landscape")

    listing = client.get("/api/plugins/procgen-studio/renders").json()["renders"]
    assert [r["id"] for r in listing] == [out.record.id]
    assert listing[0]["thumb"].startswith("data:image/jpeg;base64,")

    detail = client.get(f"/api/plugins/procgen-studio/renders/{out.record.id}").json()
    assert detail["seed"] == out.record.seed and "def draw" in detail["source"]
    assert detail["image"].startswith("data:image/png;base64,")
    assert Path(detail["paths"]["output"]).is_file()

    rerolled = client.post(f"/api/plugins/procgen-studio/renders/{out.record.id}/reroll", json={"seed": 42}).json()
    assert rerolled["seed"] == 42 and rerolled["parent"] == out.record.id and rerolled["operation"] == "reroll"

    assert client.get("/api/plugins/procgen-studio/renders/pg-00000000").status_code == 404
    assert client.post(f"/api/plugins/procgen-studio/renders/{out.record.id}/reroll",
                       json={"seed": "abc"}).status_code == 400
    assert "style" in client.get("/api/plugins/procgen-studio/status").json()
