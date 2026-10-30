"""Object storage for documents. Private, encrypted, UK region only.

Files are uploaded by the browser straight to storage with a short-lived
presigned URL, so file bytes never pass through the API. Keys never contain
filenames, which can hold personal data.
"""

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

# Non-negotiable: client data stays in the UK. eu-west-2 is AWS London.
UK_REGIONS = frozenset({"eu-west-2"})


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    # Hex SHA-256 when the store keeps one. The malware scan always checks it.
    sha256: str | None


class Storage(Protocol):
    def presign_put(self, key: str, content_type: str, size: int, sha256: str,
                    expires_in: int) -> tuple[str, dict[str, str]]: ...
    def presign_get(self, key: str, expires_in: int, filename: str | None = None) -> str: ...
    def stat(self, key: str) -> ObjectInfo | None: ...
    def get(self, key: str) -> bytes: ...
    def put(self, key: str, data: bytes, content_type: str) -> None: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class S3Storage:
    def __init__(self, bucket: str, region: str, kms_key_id: str | None = None, client=None):
        if region not in UK_REGIONS:
            raise ValueError(f"storage region {region!r} is not in the UK")
        if client is None:
            import boto3
            client = boto3.client("s3", region_name=region)
        self.s3 = client
        self.bucket = bucket
        self.kms_key_id = kms_key_id

    def _encryption(self) -> dict[str, str]:
        params = {"ServerSideEncryption": "aws:kms"}
        if self.kms_key_id:
            params["SSEKMSKeyId"] = self.kms_key_id
        return params

    def presign_put(self, key, content_type, size, sha256, expires_in):
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode()
        params = {"Bucket": self.bucket, "Key": key, "ContentType": content_type, "ContentLength": size,
                  "ChecksumSHA256": checksum, **self._encryption()}
        url = self.s3.generate_presigned_url("put_object", Params=params, ExpiresIn=expires_in)
        # The browser must send exactly these, or S3 refuses the upload.
        headers = {"Content-Type": content_type, "x-amz-checksum-sha256": checksum,
                   "x-amz-server-side-encryption": "aws:kms"}
        if self.kms_key_id:
            headers["x-amz-server-side-encryption-aws-kms-key-id"] = self.kms_key_id
        return url, headers

    def presign_get(self, key, expires_in, filename=None):
        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        return self.s3.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_in)

    def stat(self, key):
        try:
            head = self.s3.head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
        except self.s3.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        checksum = head.get("ChecksumSHA256")
        return ObjectInfo(head["ContentLength"], base64.b64decode(checksum).hex() if checksum else None)

    def get(self, key):
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put(self, key, data, content_type):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                           ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode(),
                           **self._encryption())


class LocalStorage:
    """Files on local disk, for development and tests only. Presigned URLs are
    HMAC-signed but nothing serves them; tests write with put()."""

    def __init__(self, root: Path, base_url: str = "http://localhost:9000/dev-storage", secret: bytes = b"dev"):
        self.root = Path(root)
        self.base_url = base_url
        self.secret = secret

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("key escapes the storage root")
        return path

    def _sign(self, method: str, key: str, expires_in: int) -> str:
        expires = int(time.time()) + expires_in
        signature = hmac.new(self.secret, f"{method}:{key}:{expires}".encode(), "sha256").hexdigest()
        return f"{self.base_url}/{quote(key)}?expires={expires}&signature={signature}"

    def presign_put(self, key, content_type, size, sha256, expires_in):
        return self._sign("PUT", key, expires_in), {"Content-Type": content_type}

    def presign_get(self, key, expires_in, filename=None):
        return self._sign("GET", key, expires_in)

    def stat(self, key):
        path = self._path(key)
        if not path.exists():
            return None
        data = path.read_bytes()
        return ObjectInfo(len(data), sha256_hex(data))

    def get(self, key):
        return self._path(key).read_bytes()

    def put(self, key, data, content_type):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
