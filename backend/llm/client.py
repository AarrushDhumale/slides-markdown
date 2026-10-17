"""One OpenAI-compatible client for every backend (vLLM, Ollama, OpenRouter, Gemini), plus `chain`
backends that fail over between them."""

from __future__ import annotations

import base64
import json
import os
import random
import re
import threading
import time
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import TypeVar

import httpx
import json_repair
import openai
import yaml
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from backend.llm import usage
from backend.llm.remote import VllmRemote, open_tunnel
from backend.llm.usage import BudgetExceeded, RateLimiter
from backend.paths import BACKENDS_FILE

T = TypeVar("T", bound=BaseModel)
OLLAMA_RETRIES = 4
API_RETRIES = 6          # per request on 429 / 5xx / network errors, with backoff (free tiers need patience)
MAX_BACKOFF = 60
RETRYABLE = (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError,
             openai.InternalServerError)
# `options:` keys sent as standard OpenAI parameters; anything else goes through extra_body
# (OpenRouter accepts top_k / repetition_penalty / min_p there; Ollama's name is repeat_penalty).
OPENAI_PARAMS = {"presence_penalty", "frequency_penalty", "top_p", "seed"}
EXTRA_PARAM_NAMES = {"repeat_penalty": "repetition_penalty"}


class LLMError(RuntimeError):
    """A backend failed in a way retrying the same request won't fix."""


def _backoff(attempt: int, err: Exception) -> float:
    """Seconds to wait before retry `attempt` (0-based): Retry-After when given, else exponential + jitter."""
    resp = getattr(err, "response", None)
    if resp is not None:
        try:
            return min(MAX_BACKOFF, float(resp.headers.get("retry-after", "")) + 0.5)
        except ValueError:
            pass
    return min(MAX_BACKOFF, 2 * 2 ** attempt) * (0.75 + random.random() / 2)


def _text_only(messages: list[dict]) -> list[dict]:
    """Flatten multimodal messages for a backend without vision (image parts are dropped)."""
    out = []
    for m in messages:
        if isinstance(m["content"], list):
            m = {**m, "content": "\n".join(p["text"] for p in m["content"] if p["type"] == "text")}
        out.append(m)
    return out


@cache
def load_config() -> dict:
    return yaml.safe_load(BACKENDS_FILE.read_text())


@cache
def get_remote(name: str) -> VllmRemote:
    cfg = load_config()["backends"][name]
    if cfg["kind"] != "vllm_remote":
        raise ValueError(f"backend {name!r} is not a vllm_remote backend")
    return VllmRemote(name, cfg)


def image_part(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}


def _strip_fences(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", text, flags=re.S)
    return m.group(1) if m else text


class LLM:
    def __init__(self, backend: str, stage: str | None = None):
        self.backend = backend
        self.stage = stage
        self.cfg = load_config()["backends"][backend]
        self.max_concurrency = self.cfg.get("max_concurrency", 8)
        # hosted APIs are billed unless marked `free: true` (e.g. a Gemini free-tier key)
        self.paid = self.cfg["kind"] == "openai" and not self.cfg.get("free")
        self.limiter = RateLimiter.for_backend(backend, self.cfg.get("rpm"))
        self._client: OpenAI | None = None
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        """Stable id used in cache keys."""
        return self.cfg["model"]

    @property
    def generation_id(self) -> str:
        """model_id plus sampling options: for caches of generated prose."""
        opts = self.cfg.get("options")
        return f"{self.model_id}|{json.dumps(opts, sort_keys=True)}" if opts else self.model_id

    def connect(self) -> None:
        """Bring the backend up before fanning out to worker threads."""
        if self.cfg["kind"] == "ollama":
            self._ollama_url()
        else:
            self._ensure_client()

    def _ensure_client(self) -> OpenAI:
        with self._lock:
            if not self._client:
                self._client = self._make_client()
        return self._client

    def _make_client(self) -> OpenAI:
        if self.cfg["kind"] == "vllm_remote":
            base_url = get_remote(self.backend).ensure_up()
            api_key, self._model = "unused", "s2m"
        else:
            base_url = self.cfg["base_url"]
            key_env = self.cfg.get("api_key_env", "")
            api_key = os.environ.get(key_env, "")
            if key_env and not api_key:
                raise LLMError(f"{self.backend}: set {key_env} (e.g. in .env) to use this backend")
            self._model = self.cfg["model"]
        # retries are ours (see _chat_openai): the SDK's are too short for free-tier rate limits
        return OpenAI(base_url=base_url, api_key=api_key or "unused", timeout=self.cfg.get("timeout", 600),
                      max_retries=0 if self.cfg["kind"] == "openai" else 3)

    def _ollama_url(self) -> str:
        """Native Ollama API base, tunnelling to `host` when the daemon is remote."""
        with self._lock:
            if not getattr(self, "_ollama_base", None):
                port = self.cfg.get("local_port", 11434)
                if self.cfg.get("host"):
                    open_tunnel(self.cfg["host"], port, self.cfg.get("remote_port", 11434))
                self._ollama_base = f"http://127.0.0.1:{port}"
        return self._ollama_base

    def _ollama_body(self, messages: list[dict], max_tokens: int, temperature: float,
                     schema: type[BaseModel] | None, stream: bool) -> dict:
        native = []
        for m in messages:
            if isinstance(m["content"], str):
                native.append({"role": m["role"], "content": m["content"]})
                continue
            text = "\n".join(p["text"] for p in m["content"] if p["type"] == "text")
            images = [p["image_url"]["url"].split(",", 1)[1] for p in m["content"] if p["type"] == "image_url"]
            native.append({"role": m["role"], "content": text, "images": images})
        body = {
            "model": self.cfg["model"], "messages": native, "stream": stream,
            "options": {**self.cfg.get("options", {}),
                        "num_ctx": self.cfg.get("num_ctx", 32768), "num_predict": max_tokens,
                        "temperature": temperature},
            "keep_alive": self.cfg.get("keep_alive", "10m"),
        }
        if "think" in self.cfg:
            body["think"] = self.cfg["think"]
        if schema is not None:
            body["format"] = schema.model_json_schema()
        return body

    def _ollama_error(self, r: httpx.Response) -> None:
        if r.status_code == 404 and "not found" in r.text:
            raise LLMError(f"model {self.cfg['model']!r} is not installed in Ollama on "
                           f"{self.cfg.get('host', 'localhost')} (run: ollama pull {self.cfg['model']})")

    def _chat_ollama(self, messages: list[dict], max_tokens: int, temperature: float,
                     schema: type[BaseModel] | None) -> str:
        body = self._ollama_body(messages, max_tokens, temperature, schema, stream=False)
        url = f"{self._ollama_url()}/api/chat"
        for attempt in range(OLLAMA_RETRIES):
            try:
                r = httpx.post(url, json=body, timeout=900)
                self._ollama_error(r)
                r.raise_for_status()
                data = r.json()
                self._record(data.get("prompt_eval_count", 0), data.get("eval_count", 0))
                return _strip_fences(data["message"]["content"])
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    raise LLMError(f"Ollama rejected the request: {e.response.text[:300]}") from e
                if attempt == OLLAMA_RETRIES - 1:
                    raise LLMError(f"Ollama unreachable after {OLLAMA_RETRIES} tries: {e}") from e
                time.sleep(5 * 2 ** attempt)  # model (re)loading, tunnel hiccup, OOM restart
        raise AssertionError("unreachable")

    def _openai_kwargs(self, schema: type[BaseModel] | None) -> dict:
        kwargs: dict = {}
        extra = dict(self.cfg.get("extra_body") or {})
        for key, value in (self.cfg.get("options") or {}).items():
            if key in OPENAI_PARAMS:
                kwargs[key] = value
            else:
                extra[EXTRA_PARAM_NAMES.get(key, key)] = value
        mode = self.cfg.get("response_format", "json_schema")
        if schema is not None and mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        elif schema is not None and mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        if extra:
            kwargs["extra_body"] = extra
        return kwargs

    def _create(self, **kwargs):
        """One completion request with our retry policy: back off on 429/5xx/network errors,
        fail fast (LLMError) on other 4xx — a bad key or unknown model won't fix itself."""
        client = self._ensure_client()
        retries = self.cfg.get("retries", API_RETRIES)
        for attempt in range(retries):
            try:
                return client.chat.completions.create(model=self._model, **kwargs)
            except RETRYABLE as e:
                if attempt == retries - 1:
                    raise LLMError(f"{self.backend}: gave up after {retries} tries: {e}") from e
                time.sleep(_backoff(attempt, e))
            except openai.APIStatusError as e:
                raise LLMError(f"{self.backend} rejected the request ({e.status_code}): {str(e)[:300]}") from e
        raise AssertionError("unreachable")

    def _record(self, prompt_tokens: int, completion_tokens: int, reported_usd: float | None = None) -> None:
        usd = reported_usd if reported_usd is not None else (
            usage.cost(self.cfg.get("price"), prompt_tokens, completion_tokens) if self.paid else 0.0)
        usage.record(backend=self.backend, model=self.model_id, stage=self.stage,
                     prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, usd=usd)

    def _record_openai(self, u) -> None:
        if u is None:
            return
        reported = (getattr(u, "model_extra", None) or {}).get("cost")  # OpenRouter reports the billed $
        self._record(u.prompt_tokens or 0, u.completion_tokens or 0,
                     float(reported) if reported is not None and self.paid else None)

    def _cap(self, max_tokens: int) -> int:
        """`max_output` in backends.yaml bounds a runaway generation (a small local model stuck in a loop
        would otherwise grind on to the stage's full budget)."""
        return min(max_tokens, self.cfg.get("max_output", max_tokens))

    def _before_call(self, messages: list[dict]) -> list[dict]:
        usage.check_budget(self.paid)
        if self.limiter:
            self.limiter.wait()
        return messages if self.cfg.get("vision") or self.cfg["kind"] != "openai" else _text_only(messages)

    def chat(self, messages: list[dict], *, max_tokens: int = 4096, temperature: float = 0.3,
             schema: type[BaseModel] | None = None) -> str:
        messages = self._before_call(messages)
        max_tokens = self._cap(max_tokens)
        if self.cfg["kind"] == "ollama":
            return self._chat_ollama(messages, max_tokens, temperature, schema)
        resp = self._create(messages=messages, max_tokens=max_tokens, temperature=temperature,
                            **self._openai_kwargs(schema))
        self._record_openai(resp.usage)
        return _strip_fences(resp.choices[0].message.content or "")

    def stream(self, messages: list[dict], *, max_tokens: int = 700, temperature: float = 0.3) -> Iterator[str]:
        """Yield the reply in text chunks as it is generated (for the "Explain this" popover)."""
        messages = self._before_call(messages)
        max_tokens = self._cap(max_tokens)
        if self.cfg["kind"] == "ollama":
            body = self._ollama_body(messages, max_tokens, temperature, None, stream=True)
            try:
                with httpx.stream("POST", f"{self._ollama_url()}/api/chat", json=body, timeout=300) as r:
                    if r.status_code >= 400:
                        r.read()
                        self._ollama_error(r)
                        raise LLMError(f"Ollama rejected the request: {r.text[:300]}")
                    for line in r.iter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if chunk := data.get("message", {}).get("content"):
                            yield chunk
                        if data.get("done"):
                            self._record(data.get("prompt_eval_count", 0), data.get("eval_count", 0))
            except httpx.TransportError as e:
                raise LLMError(f"Ollama unreachable: {e}") from e
            return
        resp = self._create(messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True,
                            stream_options={"include_usage": True}, **self._openai_kwargs(None))
        for chunk in resp:
            if chunk.choices and (text := chunk.choices[0].delta.content):
                yield text
            if getattr(chunk, "usage", None):
                self._record_openai(chunk.usage)

    def structured(self, messages: list[dict], schema: type[T], *, max_tokens: int = 4096,
                   temperature: float = 0.1) -> T:
        """Chat with JSON-schema output; one corrective retry on invalid JSON."""
        # Spell the schema out in the prompt too: some backends (Ollama with newer architectures)
        # don't enforce `format`, and then field names/shapes drift.
        messages = with_schema_instructions(messages, schema)
        # 1) normal attempt  2) fresh greedy attempt (broken JSON is usually a sampling derail, e.g. a
        # string that never closes — regenerating beats repairing)  3) ask the model to repair it.
        text = self.chat(messages, max_tokens=max_tokens, temperature=temperature, schema=schema)
        try:
            return parse_json_reply(text, schema)
        except ValidationError:
            pass
        text = self.chat(messages, max_tokens=max_tokens, temperature=0.0, schema=schema)
        try:
            return parse_json_reply(text, schema)
        except ValidationError as e:
            retry = messages + [
                {"role": "assistant", "content": text[:6000]},
                {"role": "user", "content": f"That was not valid JSON for the schema ({e.errors()[:3]}). "
                                            "Reply with only the corrected, complete JSON object."},
            ]
            text = self.chat(retry, max_tokens=max_tokens, temperature=0.0, schema=schema)
            return parse_json_reply(text, schema)


def close_unterminated_strings(text: str) -> str:
    """Models sometimes end a long string value (e.g. a transcribed table) with a raw newline and no
    closing quote. Close it when the next line clearly starts a new JSON token."""
    lines = text.split("\n")
    for i in range(len(lines) - 1):
        quotes = len(re.findall(r'(?<!\\)"', lines[i]))
        nxt = next((ln.strip() for ln in lines[i + 1:] if ln.strip()), "")
        if quotes % 2 == 1 and re.match(r'^(\]|\}|"[^"]*"\s*:)', nxt):
            lines[i] = lines[i].rstrip().rstrip(",") + '"' + ("," if re.match(r'^"', nxt) else "")
    return "\n".join(lines)


def parse_json_reply(text: str, schema: type[T]) -> T:
    """Validate a model's JSON reply, tolerating what models commonly emit: raw newlines/tabs inside
    strings (e.g. a transcribed table) and prose or fences around the object."""
    try:
        return schema.model_validate_json(text)
    except ValidationError as strict_error:
        candidates = [text]
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            candidates.append(text[start:end + 1])
        candidates += [close_unterminated_strings(c) for c in candidates]
        for candidate in candidates:
            try:
                return schema.model_validate(json.loads(candidate, strict=False))
            except (json.JSONDecodeError, ValidationError):
                continue
        # last resort: repair unterminated strings / missing commas (common in long table transcriptions)
        try:
            repaired = json_repair.loads(candidates[-1])
            if isinstance(repaired, dict) and repaired:
                return schema.model_validate(repaired)
        except (ValueError, ValidationError):
            pass
        raise strict_error


def with_schema_instructions(messages: list[dict], schema: type[BaseModel]) -> list[dict]:
    note = ("\n\nRespond with exactly one JSON object that conforms to this JSON Schema "
            "(same field names, strings where it says string, no extra wrapping):\n"
            + json.dumps(schema.model_json_schema(), separators=(",", ":")))
    out = [dict(m) for m in messages]
    if out and out[0]["role"] == "system" and isinstance(out[0]["content"], str):
        out[0]["content"] += note
    else:
        out.insert(0, {"role": "system", "content": note.strip()})
    return out


FAILOVER_ERRORS = (LLMError, openai.APIError, httpx.HTTPError)


class ChainLLM:
    """A `kind: chain` backend: an ordered list of backends tried in turn.

    Each member already retries transient errors itself; when one gives up (or is misconfigured,
    e.g. a missing key), the chain moves to the next member and stays there for the rest of the
    run. Sticky failover keeps one model per stage, and `model_id` / `generation_id` follow the
    active member, so later cache entries are keyed by the model that actually wrote them.
    Spend caps (BudgetExceeded) stop the job instead of failing over.
    """

    def __init__(self, backend: str, members: list[str], stage: str | None = None):
        self.backend = backend
        self.stage = stage
        self.members = [make_llm(m, stage) for m in members]
        self._active = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> LLM:
        return self.members[self._active]

    @property
    def cfg(self) -> dict:
        return self.active.cfg

    @property
    def model_id(self) -> str:
        return self.active.model_id

    @property
    def generation_id(self) -> str:
        return self.active.generation_id

    @property
    def max_concurrency(self) -> int:
        return self.active.max_concurrency

    def _fail_over(self, member: LLM, err: Exception) -> None:
        with self._lock:
            if self.members[self._active] is not member:
                return  # another thread already moved past this member
            if self._active + 1 >= len(self.members):
                raise LLMError(f"{self.backend}: every backend failed; last ({member.backend}): {err}") from err
            self._active += 1
            nxt = self.members[self._active].backend
        print(f"\n  ! {member.backend} failed ({type(err).__name__}: {str(err)[:160]}); switching to {nxt}")

    def _call(self, fn):
        while True:
            member = self.active
            try:
                return fn(member)
            except BudgetExceeded:
                raise
            except FAILOVER_ERRORS as e:
                self._fail_over(member, e)

    def connect(self) -> None:
        self._call(lambda m: m.connect())

    def chat(self, messages: list[dict], **kw) -> str:
        return self._call(lambda m: m.chat(messages, **kw))

    def structured(self, messages: list[dict], schema: type[T], **kw) -> T:
        return self._call(lambda m: m.structured(messages, schema, **kw))

    def stream(self, messages: list[dict], **kw) -> Iterator[str]:
        """Fails over only before the first chunk; a reply that breaks mid-stream raises."""
        while True:
            member = self.active
            try:
                gen = member.stream(messages, **kw)
                first = next(gen, None)
            except BudgetExceeded:
                raise
            except FAILOVER_ERRORS as e:
                self._fail_over(member, e)
                continue
            if first is not None:
                yield first
                yield from gen
            return


def make_llm(name: str, stage: str | None = None) -> LLM | ChainLLM:
    cfg = load_config()["backends"][name]
    if cfg["kind"] == "chain":
        return ChainLLM(name, cfg["members"], stage)
    return LLM(name, stage)


def stage_llm(stage: str, override: str | None = None) -> LLM | ChainLLM:
    """The backend for a pipeline stage: `override` may be a backend name or a profile."""
    cfg = load_config()
    profile = cfg.get("profiles", {}).get(override) if override else None
    if profile is not None:
        name = profile.get(stage) or cfg["stages"][stage]
    else:
        name = override or cfg["stages"][stage]
    return make_llm(name, stage)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)
