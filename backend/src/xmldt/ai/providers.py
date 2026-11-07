"""LLM providers behind one small interface: chat(messages, tools) -> Reply.

Internal message format (OpenAI-like):
    {"role": "system"|"user"|"assistant"|"tool", "content": str,
     "tool_calls": [{"id", "name", "args"}] (assistant), "tool_call_id": str (tool)}
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

import httpx

TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DEFAULT_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}


class ProviderError(Exception):
    pass


class ModelNotFound(ProviderError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Provider(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply: ...

    def list_models(self) -> list[str]: ...


# ---- SSRF guard -------------------------------------------------------------
def check_url(url: str, *, allow_private: bool) -> str:
    """Allow only http(s) URLs; refuse private, loopback and link-local targets unless allowed."""
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password:
        raise ProviderError("Адрес API должен быть вида https://host/…")
    if u.scheme == "http" and not allow_private:
        raise ProviderError("Для внешнего API разрешён только HTTPS")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror:
        raise ProviderError("Не удалось найти адрес API") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast) and not allow_private:
            raise ProviderError("Адрес API указывает во внутреннюю сеть; это запрещено")
    return url.rstrip("/")


def _client() -> httpx.Client:
    # no redirects: a redirect could bypass the SSRF check
    return httpx.Client(timeout=TIMEOUT, follow_redirects=False)


def _raise(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        detail = resp.text[:300]
        if resp.status_code in (401, 403):
            raise ProviderError("Провайдер отклонил ключ API (401/403)")
        if resp.status_code in (400, 404) and ("model_not_found" in detail or "does not exist" in detail):
            raise ModelNotFound("Провайдер не знает такую модель")
        raise ProviderError(f"Провайдер вернул ошибку {resp.status_code}: {detail}")


# ---- OpenAI-compatible (OpenAI, Ollama /v1, LM Studio, vLLM, llama.cpp) ------
class OpenAICompatible:
    def __init__(self, name: str, base_url: str, model: str, api_key: str | None):
        self.name, self.base_url, self.model, self.api_key = name, base_url, model, api_key
        self.fallback_model: str | None = None  # used once if the configured model does not exist
        self.switched_from: str | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _body(self, messages: list[dict], tools: list[dict]) -> dict:
        msgs = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                msgs.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
                    for c in m["tool_calls"]]})
            elif m["role"] == "tool":
                msgs.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        body = {"model": self.model, "messages": msgs}
        if not self.model.startswith(("gpt-5", "o1", "o3", "o4")):  # reasoning models accept only the default
            body["temperature"] = 0
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        return body

    @staticmethod
    def _parse(data: dict) -> Reply:
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Неожиданный формат ответа модели") from None
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(tc.get("id") or f"call_{len(calls)}", tc["function"]["name"], args))
        return Reply(msg.get("content") or "", calls)

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        try:
            return self._chat(messages, tools)
        except ModelNotFound:
            if not self.fallback_model or self.fallback_model == self.model:
                raise
            self.switched_from, self.model = self.model, self.fallback_model
            return self._chat(messages, tools)

    def _chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        with _client() as c:
            r = c.post(f"{self.base_url}/chat/completions", json=self._body(messages, tools), headers=self._headers())
        _raise(r)
        return self._parse(r.json())

    def list_models(self) -> list[str]:
        with _client() as c:
            r = c.get(f"{self.base_url}/models", headers=self._headers())
        _raise(r)
        return sorted(m["id"] for m in r.json().get("data", []))


# ---- Anthropic Messages API --------------------------------------------------
class Anthropic:
    name = "anthropic"

    def __init__(self, base_url: str, model: str, api_key: str | None):
        self.base_url, self.model, self.api_key = base_url, model, api_key

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"}

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        conv: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "assistant":
                blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                           for c in m.get("tool_calls") or []]
                conv.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if conv and conv[-1]["role"] == "user" and isinstance(conv[-1]["content"], list):
                    conv[-1]["content"].append(block)
                else:
                    conv.append({"role": "user", "content": [block]})
            else:
                conv.append({"role": "user", "content": m["content"]})
        body = {"model": self.model, "max_tokens": 2000, "system": system, "messages": conv, "temperature": 0}
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                             for t in tools]
        with _client() as c:
            r = c.post(f"{self.base_url}/messages", json=body, headers=self._headers())
        _raise(r)
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        calls = [ToolCall(b["id"], b["name"], b.get("input") or {})
                 for b in data.get("content", []) if b.get("type") == "tool_use"]
        return Reply(text, calls)

    def list_models(self) -> list[str]:
        with _client() as c:
            r = c.get(f"{self.base_url}/models", headers=self._headers())
        _raise(r)
        return sorted(m["id"] for m in r.json().get("data", []))


def make_provider(provider: str, *, base_url: str | None, model: str | None, api_key: str | None,
                  allow_private: bool) -> Provider:
    if not model and provider != "ollama":
        raise ProviderError("Не выбрана модель")
    url = base_url or DEFAULT_URLS.get(provider)
    if not url:
        raise ProviderError("Не указан адрес API")
    private_ok = allow_private and provider in ("ollama", "openai_compatible")
    url = check_url(url, allow_private=private_ok)
    if provider == "anthropic":
        return Anthropic(url, model or "", api_key)
    return OpenAICompatible(provider, url, model or "", api_key)
