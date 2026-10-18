"""The model bake-off harness (eval/bakeoff.py), offline with the fake LLM."""

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

from test_pipeline import FakeLLM

SPEC = importlib.util.spec_from_file_location("bakeoff", Path(__file__).resolve().parent.parent / "eval" / "bakeoff.py")
bakeoff = importlib.util.module_from_spec(SPEC)
sys.modules["bakeoff"] = bakeoff  # dataclasses resolve annotations through sys.modules
SPEC.loader.exec_module(bakeoff)


def test_write_stage_compares_writers_on_the_same_outline(chapter_pdfs, monkeypatch, tmp_path, capsys):
    writers: dict[str, FakeLLM] = {}

    def fake_make_llm(name, stage=None):
        return writers.setdefault(name, FakeLLM())

    monkeypatch.setattr(bakeoff, "make_llm", fake_make_llm)
    args = SimpleNamespace(pdf=str(chapter_pdfs[0]), reader="reader", writers="writer_a,writer_b", sections=2,
                           level="learner", no_review=False, out=tmp_path)
    bakeoff.write_stage(args)

    out = tmp_path / chapter_pdfs[0].stem / "write"
    n = len(writers["writer_a"].writer_inputs)          # sections actually written (the fixture deck has few)
    assert n >= 1 and len(writers["writer_b"].writer_inputs) == n   # both writers got the same sections
    for name in ("writer_a", "writer_b"):
        text = (out / f"{name}.md").read_text()
        assert text.count("<!-- section ") == n and "<!-- metrics: " in text
    summary = (out / "summary.md").read_text()
    assert "| writer_a |" in summary and "| writer_b |" in summary and "words / budget" in summary
    assert writers["writer_a"].reviews == writers["writer_b"].reviews == n   # drafted, then fact-checked
    assert "topic sections" in capsys.readouterr().out
    # every image a writer's file links to exists next to it, and the source slides are shown
    for name in ("writer_a", "writer_b"):
        text = (out / f"{name}.md").read_text()
        for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
            assert (out / ref).exists(), ref
        assert "<details><summary>Source slides" in text and (out / "slides").is_dir()


def test_extract_stage_reports_per_backend_cost_and_writes_side_by_side_files(chapter_pdfs, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bakeoff, "make_llm", lambda name, stage=None: FakeLLM())
    args = SimpleNamespace(pdf=str(chapter_pdfs[0]), pages="3,4", backends="a,b", out=tmp_path)
    bakeoff.extract_stage(args)
    out = tmp_path / chapter_pdfs[0].stem
    assert json.loads((out / "p003.a.json").read_text())["title"]
    md = (out / "p003.md").read_text()
    assert "## a" in md and "## b" in md and "p003.png" in md
    assert "~$/100 slides" in capsys.readouterr().out


def test_metrics_flag_the_failures_that_matter():
    m = bakeoff.section_metrics("## T\n\nshort", 100, [], {})
    assert m["words"] == 3 and m["of_budget"] == 0.03 and bakeoff.issues(m) == []
    over = bakeoff.section_metrics("## T\n\n" + "word " * 200, 100, [], {})
    assert any("over budget" in i for i in bakeoff.issues(over))


def test_repeated_runs_do_not_inherit_each_others_token_totals(chapter_pdfs, monkeypatch, tmp_path):
    """Regression: job ids were reused across invocations, so run 2's per-slide cost included run 1's tokens."""
    from backend.llm import usage
    from backend.pipeline.extract import SlideExtract

    class Metered(FakeLLM):
        model_id = generation_id = "metered-model"   # its own extraction cache, so every read really calls the model

        def structured(self, messages, schema, **kw):
            if schema is SlideExtract:
                usage.record(backend="metered", model="m", stage="extract", prompt_tokens=100,
                             completion_tokens=10, usd=0.01)
            return super().structured(messages, schema, **kw)

    monkeypatch.setattr(bakeoff, "make_llm", lambda name, stage=None: Metered())
    for pages in ("3", "4"):
        args = SimpleNamespace(pdf=str(chapter_pdfs[0]), pages=pages, backends="metered", out=tmp_path / pages)
        totals = bakeoff.extract_stage(args)["metered"]
        # one slide read at 100 in / 10 out / $0.01, whatever ran before it
        assert (totals["slides"], totals["in"], totals["out"], round(totals["usd"], 4)) == (1, 100, 10, 0.01)
