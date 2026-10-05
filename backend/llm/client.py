"""One OpenAI-compatible client for every backend (vLLM, Ollama, OpenRouter, Gemini)."""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from functools import cache
from pathlib import Path
from typing import TypeVar

import httpx
import json_repair
import yaml
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from backend.llm.remote import VllmRemote, open_tunnel
from backend.paths import BACKENDS_FILE

T = TypeVar("T", bound=BaseModel)
OLLAMA_RETRIES = 4


class LLMError(RuntimeError):
    """A backend failed in a way retrying the same request won't fix."""


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
    def __init__(self, backend: str):
        self.backend = backend
        self.cfg = load_config()["backends"][backend]
        self.max_concurrency = self.cfg.get("max_concurrency", 8)
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
            api_key = os.environ.get(self.cfg.get("api_key_env", ""), "unused")
            self._model = self.cfg["model"]
        return OpenAI(base_url=base_url, api_key=api_key, timeout=600, max_retries=3)

    def _ollama_url(self) -> str:
        """Native Ollama API base, tunnelling to `host` when the daemon is remote."""
        with self._lock:
            if not getattr(self, "_ollama_base", None):
                port = self.cfg.get("local_port", 11434)
                if self.cfg.get("host"):
                    open_tunnel(self.cfg["host"], port, self.cfg.get("remote_port", 11434))
                self._ollama_base = f"http://127.0.0.1:{port}"
        return self._ollama_base

    def _chat_ollama(self, messages: list[dict], max_tokens: int, temperature: float,
                     schema: type[BaseModel] | None) -> str:
        native = []
        for m in messages:
            if isinstance(m["content"], str):
                native.append({"role": m["role"], "content": m["content"]})
                continue
            text = "\n".join(p["text"] for p in m["content"] if p["type"] == "text")
            images = [p["image_url"]["url"].split(",", 1)[1] for p in m["content"] if p["type"] == "image_url"]
            native.append({"role": m["role"], "content": text, "images": images})
        body = {
            "model": self.cfg["model"], "messages": native, "stream": False,
            "options": {**self.cfg.get("options", {}),
                        "num_ctx": self.cfg.get("num_ctx", 32768), "num_predict": max_tokens,
                        "temperature": temperature},
            "keep_alive": self.cfg.get("keep_alive", "10m"),
        }
        if "think" in self.cfg:
            body["think"] = self.cfg["think"]
        if schema is not None:
            body["format"] = schema.model_json_schema()
        url = f"{self._ollama_url()}/api/chat"
        for attempt in range(OLLAMA_RETRIES):
            try:
                r = httpx.post(url, json=body, timeout=900)
                if r.status_code == 404 and "not found" in r.text:
                    raise LLMError(f"model {self.cfg['model']!r} is not installed in Ollama on "
                                   f"{self.cfg.get('host', 'localhost')} (run: ollama pull {self.cfg['model']})")
                r.raise_for_status()
                return _strip_fences(r.json()["message"]["content"])
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    raise LLMError(f"Ollama rejected the request: {e.response.text[:300]}") from e
                if attempt == OLLAMA_RETRIES - 1:
                    raise LLMError(f"Ollama unreachable after {OLLAMA_RETRIES} tries: {e}") from e
                time.sleep(5 * 2 ** attempt)  # model (re)loading, tunnel hiccup, OOM restart
        raise AssertionError("unreachable")

    def chat(self, messages: list[dict], *, max_tokens: int = 4096, temperature: float = 0.3,
             schema: type[BaseModel] | None = None) -> str:
        if self.cfg["kind"] == "ollama":
            return self._chat_ollama(messages, max_tokens, temperature, schema)
        client = self._ensure_client()
        kwargs: dict = {}
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            }
        if self.cfg.get("extra_body"):
            kwargs["extra_body"] = self.cfg["extra_body"]
        resp = client.chat.completions.create(
            model=self._model, messages=messages, max_tokens=max_tokens,
            temperature=temperature, **kwargs,
        )
        return _strip_fences(resp.choices[0].message.content or "")

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


def stage_llm(stage: str, override: str | None = None) -> LLM:
    cfg = load_config()
    if override in cfg.get("profiles", {}):
        return LLM(cfg["profiles"][override][stage])
    return LLM(override or cfg["stages"][stage])


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)
