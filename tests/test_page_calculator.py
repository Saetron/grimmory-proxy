import io
import zipfile
from PIL import Image
import pytest

from app.services.page_calculator import PageCalculator


def create_sample_epub(char_count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", "<container/>")
        html_content = f"<html><body><p>{'a' * char_count}</p></body></html>"
        z.writestr("OEBPS/chapter1.xhtml", html_content)
    return buf.getvalue()


def create_sample_cbz(image_count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(1, image_count + 1):
            img_buf = io.BytesIO()
            img = Image.new("RGB", (100, 100), color="blue")
            img.save(img_buf, format="JPEG")
            z.writestr(f"page_{i:03d}.jpg", img_buf.getvalue())
    return buf.getvalue()


def test_epub_calibre_page_calculation():
    # 2048 characters with 1024 chars/page rule should yield 2 pages
    epub_bytes = create_sample_epub(2048)
    pages, dtos = PageCalculator.count_epub_pages(epub_bytes, chars_per_page=1024)
    assert pages == 2
    assert len(dtos) == 2
    assert dtos[0].number == 1
    assert dtos[1].number == 2


def test_epub_empty_fallback():
    # Empty epub should yield at least 1 page
    epub_bytes = create_sample_epub(0)
    pages, dtos = PageCalculator.count_epub_pages(epub_bytes, chars_per_page=1024)
    assert pages == 1
    assert len(dtos) == 1


def test_cbz_page_calculation():
    # 5 images = 5 pages (1 image = 1 page rule)
    cbz_bytes = create_sample_cbz(5)
    pages, dtos = PageCalculator.count_cbz_pages(cbz_bytes)
    assert pages == 5
    assert len(dtos) == 5
    assert dtos[0].number == 1
    assert dtos[0].fileName == "page_001.jpg"
    assert dtos[0].mediaType == "image/jpeg"
    assert dtos[4].number == 5
