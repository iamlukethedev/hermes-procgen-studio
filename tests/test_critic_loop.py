import json
from pathlib import Path

import pytest

from procgen_studio.engine import GenerationError, parse_directives

from conftest import FakeLlm, fenced, technique

GOOD = fenced(technique("icon_tiles.py"))
BLANK = fenced("def draw(t):\n    return svg_doc('', bg=PAL['bg'])")
BROKEN = fenced("def draw(t):\n    return svg_doc(missing_variable)")

PASS = {"score": 9, "matches_prompt": True, "follows_style": True, "issues": []}
FAIL = {"score": 4, "matches_prompt": False, "issues": ["the key is missing"], "fixes": ["add the key tile"]}


def test_first_render_passes_with_one_draw_and_one_review(make_engine):
    llm = FakeLlm([GOOD], [PASS])
    engine, _ = make_engine(llm)
    out = engine.generate("four game icons: sword, potion, shield, key", aspect_ratio="square")
    assert len(llm.complete_calls) == 1
    assert len(llm.structured_calls) == 1 and llm.structured_calls[0]["has_image"]
    assert out.verdict.passed and out.verdict.mode == "vision"
    assert Path(out.record.output).is_file()
    assert [h["attempt"] for h in out.record.critic] == [1]


@pytest.mark.parametrize("max_redraws", [0, 1, 2, 4])
def test_failing_critic_is_bounded_by_max_redraws(make_engine, max_redraws):
    llm = FakeLlm([GOOD], [FAIL])
    engine, _ = make_engine(llm, max_redraws=max_redraws)
    out = engine.generate("four game icons", aspect_ratio="square")
    assert len(llm.complete_calls) == 1 + max_redraws
    assert len(llm.structured_calls) == 1 + max_redraws
    assert len(out.record.critic) == 1 + max_redraws
    assert not out.verdict.passed
    assert Path(out.record.output).is_file(), "the best attempt is still delivered"


def test_redraw_prompt_carries_critic_issues_and_best_attempt_wins(make_engine):
    llm = FakeLlm([GOOD, GOOD, GOOD], [dict(FAIL, score=6), dict(FAIL, score=3), dict(PASS, score=8)])
    engine, _ = make_engine(llm, max_redraws=2)
    out = engine.generate("four game icons", aspect_ratio="square")
    redraw_user = llm.complete_calls[1]["messages"][1]["content"]
    assert "the key is missing" in redraw_user and "CURRENT PROGRAM" in redraw_user
    assert out.verdict.score == 8 and out.verdict.passed
    scores = [h["score"] for h in out.record.critic]
    assert scores == [6, 3, 8]


def test_best_scoring_attempt_is_kept_when_all_fail(make_engine):
    llm = FakeLlm([GOOD, GOOD, GOOD], [dict(FAIL, score=6), dict(FAIL, score=2), dict(FAIL, score=5)])
    engine, _ = make_engine(llm, max_redraws=2)
    out = engine.generate("four game icons", aspect_ratio="square")
    assert out.verdict.score == 6
    attempt_one = Path(out.record.dir / "attempts" / "01" / "frame_0000.png").read_bytes()
    assert Path(out.record.output).read_bytes() == attempt_one


def test_render_error_triggers_repair_with_traceback(make_engine):
    llm = FakeLlm([BROKEN, GOOD], [PASS])
    engine, _ = make_engine(llm, max_redraws=2)
    out = engine.generate("four game icons", aspect_ratio="square")
    repair_user = llm.complete_calls[1]["messages"][1]["content"]
    assert "missing_variable" in repair_user and "failed to render" in repair_user
    assert out.record.critic[0]["ok"] is False
    assert out.record.critic[1]["ok"] is True
    assert len(llm.complete_calls) == 2


def test_reply_without_code_is_repaired(make_engine):
    llm = FakeLlm(["I'd love to draw that! Imagine a sword...", GOOD], [PASS])
    engine, _ = make_engine(llm)
    out = engine.generate("a sword icon", aspect_ratio="square")
    assert out.verdict.passed and len(llm.complete_calls) == 2


def test_every_attempt_failing_raises_after_budget(make_engine):
    llm = FakeLlm([BROKEN], [PASS])
    engine, _ = make_engine(llm, max_redraws=2)
    with pytest.raises(GenerationError) as exc:
        engine.generate("a sword icon", aspect_ratio="square")
    assert len(llm.complete_calls) == 3
    assert len(exc.value.history) == 3


def test_blank_render_fails_without_spending_a_critic_call(make_engine):
    llm = FakeLlm([BLANK, GOOD], [PASS])
    engine, _ = make_engine(llm, max_redraws=1)
    out = engine.generate("four game icons", aspect_ratio="square")
    assert out.record.critic[0]["mode"] == "auto" and out.record.critic[0]["score"] == 0
    assert len(llm.structured_calls) == 1, "only the second (non-blank) render is reviewed by the model"
    assert out.verdict.passed


def test_vision_failure_falls_back_to_structure_critique(make_engine):
    llm = FakeLlm([GOOD], [PASS], vision_error=True)
    engine, _ = make_engine(llm)
    out = engine.generate("four game icons", aspect_ratio="square")
    assert [c["has_image"] for c in llm.structured_calls] == [True, False]
    assert out.verdict.mode == "structure"
    assert "vision critique unavailable" in out.verdict.note


def test_structure_mode_sends_the_program(make_engine):
    llm = FakeLlm([GOOD], [PASS])
    engine, _ = make_engine(llm, critic="structure")
    engine.generate("four game icons", aspect_ratio="square")
    assert llm.structured_calls[0]["has_image"] is False


def test_critic_off_and_fast_model_skip_review(make_engine):
    llm = FakeLlm([GOOD], [FAIL])
    engine, _ = make_engine(llm, critic="off")
    engine.generate("four game icons", aspect_ratio="square")
    assert llm.structured_calls == []
    llm2 = FakeLlm([GOOD], [FAIL])
    engine2, _ = make_engine(llm2)
    engine2.generate("four game icons", aspect_ratio="square", model="procgen-fast")
    assert llm2.structured_calls == []


def test_critic_exception_accepts_render_instead_of_failing(make_engine):
    llm = FakeLlm([GOOD], [RuntimeError("critic provider down")])
    engine, _ = make_engine(llm, critic="structure")
    out = engine.generate("four game icons", aspect_ratio="square")
    assert Path(out.record.output).is_file()
    assert "structure critique failed" in out.verdict.note


def test_critic_log_is_persisted(make_engine):
    llm = FakeLlm([GOOD, GOOD], [FAIL, PASS])
    engine, _ = make_engine(llm)
    out = engine.generate("four game icons", aspect_ratio="square")
    meta = json.loads((out.record.dir / "meta.json").read_text())
    assert [h["passed"] for h in meta["critic"]] == [False, True]
    assert meta["llm"]["calls"] == 4
    assert (out.record.dir / "attempts" / "01.py").is_file() and (out.record.dir / "attempts" / "02.py").is_file()


def test_directives_are_parsed_and_stripped():
    d = parse_directives("a cozy cabin [style=frost seed=42] at night [set=Hero Art]")
    assert d.values == {"style": "frost", "seed": "42"}
    assert "[set=Hero Art]" in d.prompt or "Hero" in d.prompt
    d2 = parse_directives("icons [set=dungeon-icons] [renderer=pixel]")
    assert d2.values == {"set": "dungeon-icons", "renderer": "pixel"} and d2.prompt == "icons"
    d3 = parse_directives("a chart of [x=1] values")
    assert d3.values == {} and "[x=1]" in d3.prompt


def test_seed_directive_and_style_directive_apply(make_engine):
    llm = FakeLlm([GOOD], [PASS])
    engine, _ = make_engine(llm)
    out = engine.generate("four game icons [seed=99] [style=frost] [set=loot]", aspect_ratio="square")
    assert out.record.seed == 99 and out.record.style["palette"] == "frost" and out.record.set == "loot"
    user = llm.complete_calls[0]["messages"][1]["content"]
    assert "SEED=99" in user and "frost" in user and "[seed" not in user
