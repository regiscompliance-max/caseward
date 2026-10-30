"""External services the API and worker need, built from the environment."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from caseward.ai.provider import ModelProvider
from caseward.pages import OcrEngine
from caseward.scanning import Scanner
from caseward.storage import LocalStorage, S3Storage, Storage


@dataclass
class Services:
    storage: Storage
    scanner: Scanner | None = None
    ocr: OcrEngine | None = None
    ai: ModelProvider | None = None
    # Returns the GOV.UK bank holidays feed. Injected so tests need no network.
    bank_holidays: Callable[[], dict[str, Any]] | None = None


def fetch_govuk_bank_holidays() -> dict[str, Any]:
    import httpx
    from caseward.bank_holidays import GOVUK_URL
    response = httpx.get(GOVUK_URL, timeout=30)
    response.raise_for_status()
    return response.json()


def storage_from_env() -> Storage:
    backend = os.environ.get("CASEWARD_STORAGE_BACKEND", "s3")
    if backend == "local":
        return LocalStorage(Path(os.environ["CASEWARD_LOCAL_STORAGE_DIR"]))
    return S3Storage(os.environ["CASEWARD_S3_BUCKET"], os.environ.get("CASEWARD_S3_REGION", "eu-west-2"),
                     os.environ.get("CASEWARD_S3_KMS_KEY_ID"))


def ai_from_env() -> ModelProvider | None:
    """None unless an AI provider is fully configured, including its residency
    approval. Without one, extraction jobs wait in the queue.

    CASEWARD_AI_PROVIDER=bedrock (the chosen provider): CASEWARD_AI_MODEL is an
    in-region model ID in eu-west-2, CASEWARD_AI_RESIDENCY_APPROVAL records the
    agreement, and credentials come from the task's IAM role.
    CASEWARD_AI_PROVIDER=grok: the xAI adapter, as before."""
    from caseward.ai.provider import BedrockProvider, ResidencyApproval, grok
    provider = os.environ.get("CASEWARD_AI_PROVIDER")
    if provider == "bedrock":
        return BedrockProvider(os.environ["CASEWARD_AI_MODEL"],
                               ResidencyApproval(os.environ.get("CASEWARD_AI_RESIDENCY_APPROVAL", ""), frozenset()),
                               region=os.environ.get("CASEWARD_AI_REGION", "eu-west-2"))
    if provider != "grok" and "CASEWARD_AI_API_KEY" not in os.environ:
        return None
    approval = ResidencyApproval(
        reference=os.environ.get("CASEWARD_AI_RESIDENCY_APPROVAL", ""),
        allowed_hosts=frozenset(h.strip() for h in os.environ.get("CASEWARD_AI_ALLOWED_HOSTS", "").split(",") if h.strip()),
    )
    return grok(os.environ["CASEWARD_AI_API_KEY"], os.environ["CASEWARD_AI_MODEL"], approval,
                base_url=os.environ["CASEWARD_AI_BASE_URL"])


def worker_services_from_env() -> Services:
    from caseward.pages import TesseractOcr
    from caseward.scanning import ClamdScanner
    return Services(
        storage=storage_from_env(),
        scanner=ClamdScanner(os.environ["CASEWARD_CLAMD_HOST"], int(os.environ.get("CASEWARD_CLAMD_PORT", "3310"))),
        ocr=TesseractOcr(os.environ.get("CASEWARD_OCR_LANG", "eng")),
        ai=ai_from_env(),
    )
