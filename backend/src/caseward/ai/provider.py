"""The only place Caseward calls an AI model.

Non-negotiables enforced here:
  * No provider runs without a recorded residency approval: the reference
    to the signed agreement confirming that processing stays in the UK with
    no training on our data. Without it the provider refuses to start.
  * The endpoint host must be on an allowlist set with that approval, so a
    configuration change cannot quietly send data elsewhere.
  * Every call records the provider and the exact model version that
    answered.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class Completion:
    content: dict[str, Any]
    provider: str
    model_version: str


class ModelProvider(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema_name: str, schema: dict[str, Any]) -> Completion: ...


class ProviderError(Exception):
    """The model could not be reached or gave an unusable answer. The job retries."""


class ResidencyNotApproved(Exception):
    pass


@dataclass(frozen=True)
class ResidencyApproval:
    # e.g. "xAI DPA 2026-11, UK processing addendum, signed by …"
    reference: str
    allowed_hosts: frozenset[str]

    def check(self, base_url: str) -> None:
        if not self.reference.strip():
            raise ResidencyNotApproved("no residency approval recorded for the AI provider")
        host = urlparse(base_url).hostname or ""
        if host not in self.allowed_hosts:
            raise ResidencyNotApproved(f"AI endpoint {host!r} is not on the approved list {sorted(self.allowed_hosts)}")


class OpenAICompatibleProvider:
    """Chat completions with a strict JSON schema. xAI's Grok API uses this format."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str, approval: ResidencyApproval,
                 timeout: float = 120.0, transport: httpx.BaseTransport | None = None):
        approval.check(base_url)
        self.name = name
        self.model = model
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                   headers={"Authorization": f"Bearer {api_key}"})

    def complete_json(self, system, user, schema_name, schema):
        request = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
        }
        try:
            response = self.client.post("/chat/completions", json=request)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            # The body may echo our input, so keep only the status.
            raise ProviderError(f"{self.name} returned HTTP {response.status_code}")
        body = response.json()
        try:
            content = json.loads(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"{self.name} returned an unusable answer") from exc
        version = body.get("model", self.model)
        if body.get("system_fingerprint"):
            version = f"{version}+{body['system_fingerprint']}"
        return Completion(content, self.name, version)


def grok(api_key: str, model: str, approval: ResidencyApproval, base_url: str, **kwargs) -> OpenAICompatibleProvider:
    """xAI Grok. There is no default base_url on purpose: the approved regional
    endpoint must be chosen and agreed with xAI in writing."""
    return OpenAICompatibleProvider("xai", base_url, api_key, model, approval, **kwargs)
