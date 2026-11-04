import hashlib
from pathlib import Path

from procgen_studio import commands, store
from procgen_studio.style import load_preset

from conftest import FakeLlm, fenced, technique

PASS = {"score": 9, "matches_prompt": True, "issues": []}
SAMPLE = "palette: ember\noutline: 10px ink, round joins\n"


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(engine, line):
    return commands.run_text(engine, line)


def test_reroll_is_local_and_seeded(make_engine):
    llm = FakeLlm([fenced(technique("layered_scene.py"))], [PASS])
    engine, _ = make_engine(llm)
    out = engine.generate("a moonlit forest cabin", aspect_ratio="square")
    calls_before = len(llm.complete_calls) + len(llm.structured_calls)
    text = run(engine, f"reroll {out.record.id} --seed 5")
    assert "seed 5" in text
    rerolled = store.latest(1)[0]
    assert rerolled.parent == out.record.id and rerolled.operation == "reroll" and rerolled.seed == 5
    assert len(llm.complete_calls) + len(llm.structured_calls) == calls_before, "reroll must not call the model"
    assert sha(rerolled.output) != sha(out.record.output)
    run(engine, f"reroll {rerolled.id} --seed {out.record.seed}")
    again = store.latest(1)[0]
    assert sha(again.output) == sha(out.record.output), "same program + same seed = same pixels"


def test_restyle_set_after_one_line_change(make_engine):
    style_file = Path.cwd() / "STYLE.md"
    style_file.write_text(SAMPLE, encoding="utf-8")
    llm = FakeLlm([fenced(technique("icon_tiles.py"))], [PASS])
    engine, _ = make_engine(llm)
    a = engine.generate("sword and potion icons [set=loot]", aspect_ratio="square")
    b = engine.generate("shield and key icons [set=loot]", aspect_ratio="square")
    assert a.record.style["palette"] == "ember"
    style_file.write_text(SAMPLE.replace("palette: ember", "palette: frost"), encoding="utf-8")
    calls = len(llm.complete_calls)
    text = run(engine, "restyle --set loot")
    assert "Restyled 2" in text and "frost" in text
    assert len(llm.complete_calls) == calls
    restyled = [r for r in store.latest(2)]
    assert {r.parent for r in restyled} == {a.record.id, b.record.id}
    assert all(r.style["palette"] == "frost" for r in restyled)
    from procgen_studio.render import image_stats
    frost = load_preset("frost").palette
    assert image_stats(Path(restyled[0].output), frost)["palette_adherence"] > 0.8


def test_regen_set_calls_the_model_per_asset(make_engine):
    llm = FakeLlm([fenced(technique("icon_tiles.py"))], [PASS])
    engine, _ = make_engine(llm)
    run(engine, "set Dungeon Loot")
    engine.generate("sword icon", aspect_ratio="square")
    engine.generate("potion icon", aspect_ratio="square")
    before = len(llm.complete_calls)
    text = run(engine, "regen --set dungeon-loot --style frost")
    assert "Regenerated 2" in text
    assert len(llm.complete_calls) == before + 2
    assert all(r.style["palette"] == "frost" for r in store.latest(2))


def test_style_commands_and_init(make_engine):
    engine, ctx = make_engine(FakeLlm([fenced(technique("icon_tiles.py"))], [PASS]))
    assert "ember" in run(engine, "styles")
    assert "Active preset: frost" in run(engine, "style frost")
    assert ctx._state["preset"] == "frost"
    assert "PAL['gem']" in run(engine, "style show")
    assert "unknown preset" in run(engine, "style nope")
    msg = run(engine, "init ember")
    assert "Wrote" in msg
    text = (Path.cwd() / "STYLE.md").read_text()
    assert "palette: ember" in text
    assert engine.current_style().source == "file"
    assert "already exists" in run(engine, "init frost")


def test_list_show_source_status_help(make_engine):
    engine, _ = make_engine(FakeLlm([fenced(technique("brand_mark.py"))], [PASS]))
    assert "No renders yet" in run(engine, "list")
    out = engine.generate("a logo", aspect_ratio="square")
    assert out.record.id in run(engine, "list")
    shown = run(engine, "show last")
    assert out.record.id in shown and "seed=" in shown and "critic:" in shown
    assert "def draw" in run(engine, f"source {out.record.id}")
    assert "style in effect" in run(engine, "status")
    assert "reroll" in run(engine, "help") and "reroll" in run(engine, "")
    assert "not a render id" in run(engine, "show pg-zz") or "not a Procgen" in run(engine, "show pg-zz")
    assert "procgen" in run(engine, "frobnicate")


def test_edit_command(make_engine):
    llm = FakeLlm([fenced(technique("brand_mark.py")),
                   fenced(technique("brand_mark.py").replace("forgeline", "emberline"))], [PASS])
    engine, _ = make_engine(llm)
    engine.generate("a logo for forgeline", aspect_ratio="square")
    text = run(engine, "edit last rename it emberline")
    assert "Edited" in text
    assert store.latest(1)[0].operation == "edit"


def test_render_local_file_and_gallery(make_engine, tmp_path):
    engine, _ = make_engine(FakeLlm([fenced(technique("brand_mark.py"))], [PASS]))
    src = tmp_path / "topo.py"
    src.write_text(technique("topo_pattern.py"), encoding="utf-8")
    text = run(engine, f"render {src} --seed 3 --style frost --size 256x256")
    assert "Rendered" in text and src.with_suffix(".png").is_file()
    engine.generate("a logo", aspect_ratio="square")
    assert "Gallery of 1" in run(engine, "gallery")


def test_cli_handler_prints(make_engine, capsys):
    engine, _ = make_engine(FakeLlm([fenced(technique("brand_mark.py"))], [PASS]))
    handler = commands.cli_handler_factory(lambda: engine)

    class NS:
        args = ["styles"]
    assert handler(NS()) == 0
    assert "frost" in capsys.readouterr().out
