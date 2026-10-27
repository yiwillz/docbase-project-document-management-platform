import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gen_erd.py"


def test_erd_up_to_date():
    """改了 models.py 却没重新生成关系图时，这里会失败：运行 python scripts/gen_erd.py。"""
    spec = importlib.util.spec_from_file_location("gen_erd", SCRIPT)
    gen_erd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen_erd)
    assert gen_erd.OUTPUT.read_text(encoding="utf-8") == gen_erd.render()
