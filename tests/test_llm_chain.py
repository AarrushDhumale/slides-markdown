"""LLM layer robustness, offline: retries, failover chains, sampling options, usage ledger, spend caps."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from backend.llm import client, usage

CONFIG = {
    "stages": {"extract": "chain", "outline": "paid", "write": "paid"},
    "profiles": {"pair": {"extract": "paid", "write": "free"}},
    "backends": {
        "paid": {"kind": "openai", "base_url": "http://paid", "model": "paid-model", "vision": False,
                 "price": [1.0, 2.0], "retries": 3,
                 "options": {"presence_penalty": 0.0, "repeat_penalty": 1.05, "top_k": 20, "top_p": 0.9},
                 "extra_body": {"reasoning": {"enabled": False}}},
        "free": {"kind": "openai", "base_url": "http://free", "model": "free-model", "vision": True,
                 "free": True, "retries": 2},
        "chain": {"kind": "chain", "members": ["paid", "free"]},
    },
}


def _req():
    return httpx.Request("POST", "http://x/chat/completions")


def _reply(text="ok", prompt=1000, completion=500, cost=None):
    extra = {"cost": cost} if cost is not None else {}
    u = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, model_extra=extra)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=u)


class FakeCompletions:
    """Scripted responses: each item is a reply or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "load_config", lambda: CONFIG)
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    monkeypatch.setattr(usage, "LEDGER", tmp_path / "usage.jsonl")
    monkeypatch.setattr(usage, "_month", None)
    monkeypatch.setattr(usage, "_job_spend", usage.defaultdict(float))
    monkeypatch.delenv("S2M_MAX_USD_PER_JOB", raising=False)
    monkeypatch.delenv("S2M_MAX_USD_PER_MONTH", raising=False)
    usage.set_job(None)


def scripted(llm, *script):
    fake = FakeCompletions(script)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    llm._model = llm.cfg["model"]
    return fake


def ledger(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_options_and_thinking_reach_openai_backends():
    llm = client.LLM("paid", "write")
    fake = scripted(llm, _reply())
    llm.chat([{"role": "user", "content": "hi"}])
    call = fake.calls[0]
    assert call["presence_penalty"] == 0.0 and call["top_p"] == 0.9
    assert call["extra_body"] == {"reasoning": {"enabled": False}, "repetition_penalty": 1.05, "top_k": 20}
    assert "options" in llm.generation_id or llm.generation_id != llm.model_id  # sampling is part of cache keys


def test_text_only_backend_gets_images_stripped():
    llm = client.LLM("paid")
    fake = scripted(llm, _reply())
    msg = {"role": "user", "content": [{"type": "text", "text": "look"},
                                       {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}
    llm.chat([msg])
    assert fake.calls[0]["messages"][0]["content"] == "look"


def test_rate_limit_backs_off_then_succeeds():
    llm = client.LLM("paid")
    err = openai.RateLimitError("slow down", response=httpx.Response(429, request=_req(),
                                                                       headers={"retry-after": "7"}), body=None)
    fake = scripted(llm, err, _reply("done"))
    assert llm.chat([{"role": "user", "content": "x"}]) == "done"
    assert len(fake.calls) == 2
    assert client._backoff(0, err) == 7.5  # Retry-After honoured


def test_bad_request_fails_fast():
    llm = client.LLM("paid")
    err = openai.BadRequestError("bad", response=httpx.Response(400, request=_req()), body=None)
    fake = scripted(llm, err, _reply())
    with pytest.raises(client.LLMError, match="rejected"):
        llm.chat([{"role": "user", "content": "x"}])
    assert len(fake.calls) == 1


def test_chain_fails_over_and_sticks(capsys):
    chain = client.stage_llm("extract")
    assert isinstance(chain, client.ChainLLM) and chain.model_id == "paid-model"
    paid, free = chain.members
    down = openai.InternalServerError("boom", response=httpx.Response(503, request=_req()), body=None)
    scripted(paid, down, down, down)
    fake_free = scripted(free, _reply("from free"), _reply("again"))
    assert chain.chat([{"role": "user", "content": "x"}]) == "from free"
    assert chain.model_id == "free-model" and chain.cfg["vision"] is True  # cache keys follow the active model
    assert chain.chat([{"role": "user", "content": "y"}]) == "again"       # sticky: paid isn't retried
    assert len(fake_free.calls) == 2
    assert "switching to free" in capsys.readouterr().out


def test_chain_exhausted_raises():
    chain = client.stage_llm("extract")
    bad = openai.AuthenticationError("no", response=httpx.Response(401, request=_req()), body=None)
    scripted(chain.members[0], bad)
    scripted(chain.members[1], openai.AuthenticationError("no", response=httpx.Response(401, request=_req()),
                                                          body=None))
    with pytest.raises(client.LLMError, match="every backend failed"):
        chain.chat([{"role": "user", "content": "x"}])


def test_missing_key_fails_over(monkeypatch):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["backends"]["paid"]["api_key_env"] = "S2M_TEST_MISSING_KEY"
    monkeypatch.setattr(client, "load_config", lambda: cfg)
    monkeypatch.delenv("S2M_TEST_MISSING_KEY", raising=False)
    chain = client.stage_llm("extract")
    scripted(chain.members[1], _reply("fallback"))
    chain.connect()
    assert chain.active.backend == "free"


def test_profile_and_stage_resolution():
    assert client.stage_llm("write", "pair").backend == "free"
    assert client.stage_llm("outline", "pair").backend == "paid"   # stage missing from profile -> default
    assert client.stage_llm("write", "free").backend == "free"     # a plain backend name


def test_usage_ledger_and_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("S2M_MAX_USD_PER_JOB", "10")
    usage.set_job("job-1")
    paid = client.LLM("paid", "write")
    scripted(paid, _reply(prompt=1_000_000, completion=500_000), _reply(cost=0.0123))
    paid.chat([{"role": "user", "content": "x"}])
    paid.chat([{"role": "user", "content": "y"}])
    free = client.LLM("free", "extract")
    scripted(free, _reply())
    free.chat([{"role": "user", "content": "z"}])
    rows = ledger(tmp_path / "usage.jsonl")
    assert [r["usd"] for r in rows] == [2.0, 0.0123, 0.0]   # price table, then provider-reported, then free
    assert rows[0]["job"] == "job-1" and rows[0]["stage"] == "write"
    s = usage.summary()
    assert round(s["total"], 4) == 2.0123 and s["backends"]["paid"]["calls"] == 2
    assert round(usage.job_spend("job-1"), 4) == 2.0123


def test_budget_caps_stop_paid_calls_only(monkeypatch):
    monkeypatch.setenv("S2M_MAX_USD_PER_JOB", "1.5")
    usage.set_job("job-2")
    paid = client.LLM("paid")
    fake = scripted(paid, _reply(prompt=1_000_000, completion=500_000), _reply())
    paid.chat([{"role": "user", "content": "x"}])           # $2: allowed (cap checked before the call)
    with pytest.raises(usage.BudgetExceeded, match="per-job"):
        paid.chat([{"role": "user", "content": "y"}])
    assert len(fake.calls) == 1
    free = client.LLM("free")
    scripted(free, _reply("still fine"))
    assert free.chat([{"role": "user", "content": "z"}]) == "still fine"   # $0 backends are never capped
    monkeypatch.setenv("S2M_MAX_USD_PER_MONTH", "1.0")
    usage.set_job("job-3")
    with pytest.raises(usage.BudgetExceeded, match="monthly"):
        paid.chat([{"role": "user", "content": "w"}])


def test_budget_exceeded_is_not_failed_over(monkeypatch):
    monkeypatch.setenv("S2M_MAX_USD_PER_MONTH", "0")
    chain = client.stage_llm("extract")
    with pytest.raises(usage.BudgetExceeded):
        chain.chat([{"role": "user", "content": "x"}])
    assert chain.active.backend == "paid"


def test_rate_limiter_paces(monkeypatch):
    now = [0.0]
    slept = []
    monkeypatch.setattr(usage.time, "monotonic", lambda: now[0])

    def fake_sleep(s):
        slept.append(s)
        now[0] += s
    monkeypatch.setattr(usage.time, "sleep", fake_sleep)
    rl = usage.RateLimiter(rpm=2)
    rl.wait()
    rl.wait()
    rl.wait()  # third start in the same minute waits ~60 s
    assert slept and 59 < slept[0] <= 60.1


def test_stream_chain_fails_over_before_first_chunk():
    chain = client.stage_llm("extract")
    paid, free = chain.members

    def broken(messages, **kw):
        raise client.LLMError("down")
        yield  # pragma: no cover

    def ok(messages, **kw):
        yield "Hello "
        yield "world"
    paid.stream, free.stream = broken, ok
    assert "".join(chain.stream([{"role": "user", "content": "x"}])) == "Hello world"


def test_max_output_caps_runaway_generations(monkeypatch):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["backends"]["free"]["max_output"] = 100
    monkeypatch.setattr(client, "load_config", lambda: cfg)
    llm = client.LLM("free")
    fake = scripted(llm, _reply(), _reply())
    llm.chat([{"role": "user", "content": "x"}], max_tokens=6144)
    llm.chat([{"role": "user", "content": "y"}], max_tokens=50)
    assert [c["max_tokens"] for c in fake.calls] == [100, 50]
