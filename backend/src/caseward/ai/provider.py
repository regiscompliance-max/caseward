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


# Bedrock model IDs with these prefixes are cross-region or global inference
# profiles, which may process the request outside the UK.
CROSS_REGION_PREFIXES = ("eu.", "us.", "us-gov.", "apac.", "ap.", "jp.", "au.", "ca.", "global.")
UK_BEDROCK_REGIONS = frozenset({"eu-west-2"})


def check_in_region_model(model_id: str, region: str) -> None:
    if region not in UK_BEDROCK_REGIONS:
        raise ResidencyNotApproved(f"Bedrock region {region!r} is not in the UK")
    name = model_id.rsplit("/", 1)[-1] if model_id.startswith("arn:") else model_id
    if name.startswith(CROSS_REGION_PREFIXES):
        raise ResidencyNotApproved(f"{model_id!r} is a cross-region or global profile; use an in-region model ID")
    if "inference-profile" in model_id:
        # Even an application inference profile in eu-west-2 can wrap a
        # cross-region profile, and its ID does not say which. Refuse them all.
        raise ResidencyNotApproved(f"{model_id!r} is an inference profile; use an in-region model ID")
    if model_id.startswith("arn:") and not model_id.startswith(f"arn:aws:bedrock:{region}:"):
        raise ResidencyNotApproved(f"{model_id!r} is not in {region}")


class BedrockProvider:
    """Amazon Bedrock in London, in-region only (AWS: requests to an in-region
    model are processed within the Region called). Structured output uses the
    Converse API with one forced tool whose input schema is our JSON schema."""

    name = "bedrock"

    def __init__(self, model_id: str, approval: ResidencyApproval, region: str = "eu-west-2", client=None,
                 max_tokens: int = 4096):
        check_in_region_model(model_id, region)
        if not approval.reference.strip():
            raise ResidencyNotApproved("no residency approval recorded for the AI provider")
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("bedrock-runtime", region_name=region,
                                  config=Config(read_timeout=120, retries={"max_attempts": 2, "mode": "standard"}))
        self.client = client
        self.model_id = model_id
        self.max_tokens = max_tokens

    def complete_json(self, system, user, schema_name, schema):
        import botocore.exceptions
        try:
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"temperature": 0, "maxTokens": self.max_tokens},
                toolConfig={"tools": [{"toolSpec": {"name": schema_name, "description": "Record the result.",
                                                    "inputSchema": {"json": schema}}}],
                            "toolChoice": {"tool": {"name": schema_name}}},
            )
        except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as exc:
            # Error messages can quote the request; keep only the error type and code.
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
            raise ProviderError(f"bedrock call failed: {type(exc).__name__} {code}".strip()) from None
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if "toolUse" in block and block["toolUse"].get("name") == schema_name:
                return Completion(block["toolUse"]["input"], self.name, self.model_id)
        raise ProviderError(f"bedrock returned no {schema_name} result (stop reason {response.get('stopReason')})")
