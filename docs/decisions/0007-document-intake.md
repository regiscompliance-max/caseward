# 0007: Document intake and viewing

Date: 30 Oct 2026. Status: proposed; needs security lead review.

## What

- **Two-step upload straight to storage.**
  - `POST /uploads` declares the filename, type, size and SHA-256 and gets a
    15-minute presigned PUT URL. On S3 the URL requires the SHA-256 checksum
    and KMS encryption headers.
  - `POST /documents` registers the upload. It checks that the file exists
    and that its size (and checksum, where the store reports one) matches,
    then queues malware scanning.
  - File bytes never pass through the API.
- **Duplicates are refused before upload.** A file whose SHA-256 the client
  already has returns 409 `duplicate_document` with the existing document's
  id (robustness rule 6).
- **Uploads are single-use and personal.** Only the user an upload URL was
  issued to can register it, once, before it expires.
- **The pipeline is a chain of jobs**, each committed before the next is
  queued:
  - `scan_malware` downloads the file, **checks size and SHA-256**, then
    scans it with ClamAV (clamd over the network). Infected files are
    marked `infected` and never served.
  - `ocr` renders each page to an image for the viewer. It reads text from
    the PDF's own text layer where there is one, and runs OCR (Tesseract)
    otherwise. It stores page images and text in storage, and an
    `ocr_output` row that records the engine and its version.
  - `extract` is queued for certificates and hazard reports. It waits until
    the AI extraction worker exists.
- **Staff never download originals.** The operations API serves page
  images, each watermarked with the viewer's name and the time, and
  `no-store`. Every page view is audited against the client.
- **Clients may download originals**, once clean, through a presigned link
  valid for 60 seconds. The download is audited.
- **UK only.** `S3Storage` refuses any region outside the UK. Storage keys
  never contain filenames.

## Why

- Presigned uploads keep large files off the API servers and out of their
  logs.
- Storage-side checksum checks vary between providers (the S3 mock does not
  apply them), so the scan job's own check is the one we rely on.
- Watermarked page images meet "no downloads to local devices" for the
  Nairobi team without blocking their work.

## Alternatives considered

- **Upload through the API.** Simpler for clients, but 100 MB files would go
  through our servers and need streaming malware scans in the request.
- **Show staff the original PDF in a browser viewer.** The PDF would be
  cached on the remote desktop and could be saved.
- **Scan with a cloud provider's malware service.** That ties us to one
  provider, and it must also process data in the UK. clamd runs alongside
  the worker.

## Consequences

- The worker image needs Tesseract (English data) and network access to
  clamd. Both are DevOps tasks.
- The scan job reads whole files into memory (100 MB limit). Move to
  streaming if larger files are allowed.
- Email (`message/rfc822`) is scanned but not yet split into attachments.
- HEIC photos are accepted at upload but cannot be rendered yet.
