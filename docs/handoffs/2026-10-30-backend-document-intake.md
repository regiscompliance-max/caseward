HAND-OFF NOTE

Role: Backend engineer (document intake)

Date: 30 Oct 2026

What I completed (with links to files or pull requests):
- `backend/src/caseward/storage.py`: S3 storage (UK region only, KMS
  encryption, presigned uploads that require a SHA-256 checksum) and local
  storage for development.
- `backend/src/caseward/scanning.py`: ClamAV client over the clamd network
  protocol.
- `backend/src/caseward/pages.py`: PDF and image rendering (pypdfium2,
  Pillow), text from PDF text layers, a Tesseract OCR engine, watermarking,
  and page and size limits.
- `backend/src/caseward/worker/documents.py`: the `scan_malware` job (with
  an integrity check) and the `ocr` job (page images, text, `ocr_output`).
  `extract` is queued for certificates and hazard reports.
- `backend/db/migrations/0004_document_intake.sql`: uploads (single-use,
  personal, expiring), document pages, and `document_processing()`.
- API, both sides: `createUpload` (with duplicate detection) and
  `createDocument`. Client API: `listDocuments`, `getDocument`,
  `downloadDocument`. Operations API: `listDocuments` (with a processing
  filter), `getDocument` (pages, extractions) and `getDocumentPage`
  (watermarked).
- Spec: `existing_document_id` on problem responses, and 404 on
  `createDocument`.

Tests added and their status:
- `backend/tests/test_documents.py`, 21 tests. They cover:
  - a certificate going from upload to watermarked pages, with text read
    and extraction queued;
  - scans read by OCR, and photos complete once rendered;
  - duplicate uploads refused;
  - a missing upload and a size mismatch at registration;
  - a file swapped after registration failing the scan;
  - an infected file rejected and never served;
  - a scanner outage leaving documents waiting;
  - an unreadable PDF ending up failed;
  - uploads that are single-use, personal and expiring;
  - contractors uploading only for landlords that engage them;
  - audited downloads, and staff filtering by progress;
  - the clamd client against a fake clamd;
  - S3 region lock, encryption and checksum headers (moto);
  - storage keys that cannot escape the root;
  - multi-page TIFFs and unsupported formats.
- 179 tests in total, all passing, including from a clean install.

Decisions made (and decision record links):
- `docs/decisions/0007-document-intake.md`

Open questions for the founder:
1. Is 100 MB per file enough for large fire risk assessments and photo
   bundles?
2. For emailed documents, should we split attachments into separate
   documents automatically, or have a case handler do it?

Risks or problems found:
- Tesseract is not in this development environment, so OCR of scans is
  tested with a fake engine. A real scan should be tested once the worker
  image exists.
- The S3 mock ignores checksums. The scan job's own SHA-256 check is the
  safeguard, and it is tested.
- HEIC photos (common from iPhones) cannot be rendered yet. They fail at
  `ocr`. Either add `pillow-heif` or convert them in the browser.

What the next role needs to do next:
- AI extraction engineer:
  - the `extract` job: the internal AI service, versioned prompts, a strict
    JSON schema per certificate type, per-field confidence, and flags for
    contradictions;
  - an evaluation harness against the compliance expert's labelled set.
  Jobs are already queued and waiting.
- Backend: `POST /extractions/{id}/review`, which turns approved results
  into inspections and defects, and the queue task for each extraction.
- DevOps: a worker image with Tesseract, a clamd sidecar, a private S3
  bucket in eu-west-2 with KMS and the CORS that browser uploads need.
- Frontend: the upload flow (hash in the browser, PUT with the required
  headers, register) and the side-by-side page viewer.
