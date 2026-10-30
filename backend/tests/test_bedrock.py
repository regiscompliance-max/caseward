"""The Bedrock adapter: in-region only, forced structured output, quiet errors."""

import pytest
from botocore.stub import Stubber
import boto3

from caseward.ai import extraction
from caseward.ai.provider import BedrockProvider, ProviderError, ResidencyApproval, ResidencyNotApproved
from test_extraction import GOOD, TODAY

APPROVAL = ResidencyApproval("AWS DPA; Bedrock eu-west-2 in-region inference", frozenset())
MODEL = "anthropic.claude-sonnet-5-v1:0"


def client():
    return boto3.client("bedrock-runtime", region_name="eu-west-2", aws_access_key_id="x", aws_secret_access_key="x")


@pytest.mark.parametrize("model, region", [
    ("eu.anthropic.claude-sonnet-5-v1:0", "eu-west-2"),
    ("global.anthropic.claude-sonnet-5-v1:0", "eu-west-2"),
    ("arn:aws:bedrock:eu-west-2:123456789012:inference-profile/eu.anthropic.claude-sonnet-5-v1:0", "eu-west-2"),
    ("arn:aws:bedrock:eu-west-1:123456789012:foundation-model/anthropic.claude-sonnet-5-v1:0", "eu-west-2"),
    ("arn:aws:bedrock:eu-west-2:123456789012:application-inference-profile/a1b2c3", "eu-west-2"),
    (MODEL, "eu-west-1"),
])
def test_only_in_region_london_models_are_allowed(model, region):
    with pytest.raises(ResidencyNotApproved):
        BedrockProvider(model, APPROVAL, region=region, client=object())


def test_needs_a_recorded_approval():
    with pytest.raises(ResidencyNotApproved, match="no residency approval"):
        BedrockProvider(MODEL, ResidencyApproval(" ", frozenset()), client=object())


def test_in_region_model_ids_and_arns_are_accepted():
    BedrockProvider(MODEL, APPROVAL, client=object())
    BedrockProvider("arn:aws:bedrock:eu-west-2::foundation-model/anthropic.claude-sonnet-5-v1:0", APPROVAL, client=object())


def test_extraction_through_bedrock_forces_the_schema_tool():
    c = client()
    schema = extraction.response_schema("eicr")
    answer = {"fields": GOOD, "confidence": {k: 0.96 for k in GOOD}}
    with Stubber(c) as stub:
        stub.add_response("converse", {
            "output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "t1", "name": "eicr_v0", "input": answer}}]}},
            "stopReason": "tool_use", "usage": {"inputTokens": 10, "outputTokens": 10, "totalTokens": 20},
            "metrics": {"latencyMs": 5}},
            expected_params={
                "modelId": MODEL,
                "system": [{"text": extraction.prompt().format(certificate_type_name="EICR")}],
                "messages": [{"role": "user", "content": [{"text": "--- Page 1 ---\npage text"}]}],
                "inferenceConfig": {"temperature": 0, "maxTokens": 4096},
                "toolConfig": {"tools": [{"toolSpec": {"name": "eicr_v0", "description": "Record the result.",
                                                       "inputSchema": {"json": schema}}}],
                               "toolChoice": {"tool": {"name": "eicr_v0"}}},
            })
        result = extraction.extract(BedrockProvider(MODEL, APPROVAL, client=c), "eicr", "EICR", ["page text"], TODAY)
    assert result.result == GOOD
    assert (result.provider, result.model_version) == ("bedrock", MODEL)


def test_errors_do_not_carry_request_content():
    c = client()
    with Stubber(c) as stub:
        stub.add_client_error("converse", service_error_code="ValidationException",
                              service_message="Input contains tenant Pat Tenant at 1 High St", http_status_code=400)
        with pytest.raises(ProviderError) as raised:
            BedrockProvider(MODEL, APPROVAL, client=c).complete_json("s", "u", "eicr_v0", {"type": "object"})
    assert "ValidationException" in str(raised.value)
    assert "Pat Tenant" not in str(raised.value)


def test_an_answer_without_the_tool_is_an_error():
    c = client()
    with Stubber(c) as stub:
        stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": [{"text": "Sorry"}]}},
                                       "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1,
                                                                            "totalTokens": 2}, "metrics": {"latencyMs": 1}})
        with pytest.raises(ProviderError, match="no eicr_v0 result"):
            BedrockProvider(MODEL, APPROVAL, client=c).complete_json("s", "u", "eicr_v0", {"type": "object"})


def test_bedrock_is_chosen_from_the_environment(monkeypatch):
    from caseward.services import ai_from_env
    monkeypatch.setenv("CASEWARD_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("CASEWARD_AI_MODEL", MODEL)
    monkeypatch.setenv("CASEWARD_AI_RESIDENCY_APPROVAL", "AWS DPA")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    provider = ai_from_env()
    assert isinstance(provider, BedrockProvider) and provider.model_id == MODEL
    monkeypatch.setenv("CASEWARD_AI_MODEL", "eu." + MODEL)
    with pytest.raises(ResidencyNotApproved):
        ai_from_env()
