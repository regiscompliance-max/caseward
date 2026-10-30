"""External services the API and worker need, built from the environment."""

import os
from dataclasses import dataclass
from pathlib import Path

from caseward.pages import OcrEngine
from caseward.scanning import Scanner
from caseward.storage import LocalStorage, S3Storage, Storage


@dataclass
class Services:
    storage: Storage
    scanner: Scanner | None = None
    ocr: OcrEngine | None = None


def storage_from_env() -> Storage:
    backend = os.environ.get("CASEWARD_STORAGE_BACKEND", "s3")
    if backend == "local":
        return LocalStorage(Path(os.environ["CASEWARD_LOCAL_STORAGE_DIR"]))
    return S3Storage(os.environ["CASEWARD_S3_BUCKET"], os.environ.get("CASEWARD_S3_REGION", "eu-west-2"),
                     os.environ.get("CASEWARD_S3_KMS_KEY_ID"))


def worker_services_from_env() -> Services:
    from caseward.pages import TesseractOcr
    from caseward.scanning import ClamdScanner
    return Services(
        storage=storage_from_env(),
        scanner=ClamdScanner(os.environ["CASEWARD_CLAMD_HOST"], int(os.environ.get("CASEWARD_CLAMD_PORT", "3310"))),
        ocr=TesseractOcr(os.environ.get("CASEWARD_OCR_LANG", "eng")),
    )
