"""Turning documents into page images and text.

PDFs with a text layer use it directly. Scanned pages and images go through
an OCR engine (Tesseract in production). Page images are what staff see in
the side-by-side viewer, so originals never leave UK storage.
"""

import io
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence

Image.MAX_IMAGE_PIXELS = 100_000_000  # Refuse decompression bombs.

RENDERABLE = frozenset({"application/pdf", "image/jpeg", "image/png", "image/tiff"})
MAX_PAGES = 300
RENDER_DPI = 150
MAX_SIDE = 2000
# A page with less text than this in its text layer is treated as a scan.
MIN_TEXT_CHARS = 20


class UnsupportedDocument(Exception):
    pass


@dataclass
class Page:
    image: Image.Image
    text: str | None


class OcrEngine(Protocol):
    name: str
    version: str

    def read(self, image: Image.Image) -> tuple[str, float]:
        """Text and mean confidence from 0 to 1."""


class TesseractOcr:
    name = "tesseract"

    def __init__(self, lang: str = "eng"):
        import pytesseract
        self.tesseract = pytesseract
        self.lang = lang
        self.version = str(pytesseract.get_tesseract_version())

    def read(self, image):
        data = self.tesseract.image_to_data(image, lang=self.lang, output_type=self.tesseract.Output.DICT)
        words = [(w, float(c)) for w, c in zip(data["text"], data["conf"]) if w.strip() and float(c) >= 0]
        confidence = sum(c for _, c in words) / len(words) / 100 if words else 0.0
        return " ".join(w for w, _ in words), confidence


def _fit(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


def render(data: bytes, mime_type: str) -> list[Page]:
    if mime_type == "application/pdf":
        import pypdfium2
        try:
            pdf = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError as exc:
            raise UnsupportedDocument(f"unreadable PDF: {exc}") from exc
        if len(pdf) > MAX_PAGES:
            raise UnsupportedDocument(f"{len(pdf)} pages is over the {MAX_PAGES} page limit")
        pages = []
        for page in pdf:
            image = page.render(scale=RENDER_DPI / 72).to_pil()
            text = page.get_textpage().get_text_bounded().strip()
            pages.append(Page(_fit(image), text if len(text) >= MIN_TEXT_CHARS else None))
        return pages
    if mime_type in RENDERABLE:
        try:
            source = Image.open(io.BytesIO(data))
            frames = [_fit(ImageOps.exif_transpose(frame.copy())) for frame in ImageSequence.Iterator(source)]
        except (OSError, Image.DecompressionBombError) as exc:
            raise UnsupportedDocument(f"unreadable image: {exc}") from exc
        if len(frames) > MAX_PAGES:
            raise UnsupportedDocument(f"{len(frames)} pages is over the {MAX_PAGES} page limit")
        return [Page(frame, None) for frame in frames]
    raise UnsupportedDocument(f"{mime_type} cannot be rendered")


def to_webp(image: Image.Image, quality: int = 80) -> bytes:
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=quality)
    return out.getvalue()


def watermark(webp: bytes, label: str) -> bytes:
    """Stamp the viewer's name and the time across the page, so a photo of the
    screen still shows who looked at it and when."""
    image = Image.open(io.BytesIO(webp)).convert("RGBA")
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = ImageFont.load_default(size=max(14, image.width // 45))
    step = max(120, image.height // 8)
    for y in range(step // 2, image.height, step):
        draw.text((image.width // 20, y), label, fill=(200, 0, 0, 70), font=font)
    out = io.BytesIO()
    Image.alpha_composite(image, layer).convert("RGB").save(out, format="WEBP", quality=80)
    return out.getvalue()
