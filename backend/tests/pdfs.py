"""Build small test PDFs with reportlab."""

from pathlib import Path

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def _image_page(c: canvas.Canvas) -> None:
    from PIL import Image

    c.drawImage(ImageReader(Image.new("RGB", (40, 40), "black")), 100, 100, 200, 200)


def build_pdf(path: Path, pages: list[list[str] | str]) -> Path:
    """Each page is a list of lines, "" for a blank page or "image" for an image-only page."""
    c = canvas.Canvas(str(path))
    for page in pages:
        if page == "image":
            _image_page(c)
        elif page:
            y = 780
            for line in page:
                c.drawString(72, y, line)
                y -= 20
        c.showPage()
    c.save()
    return path


def text_page(*lines: str) -> list[str]:
    return list(lines)


def slide_page(title: str, *bullets: str) -> list[str]:
    return [title, *bullets]
