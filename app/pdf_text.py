import io
import os
import shutil
from pathlib import Path

import pytesseract
from pdf2image import convert_from_bytes, convert_from_path
from pdf2image.exceptions import (
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFPopplerTimeoutError,
    PDFSyntaxError,
)
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pytesseract import TesseractNotFoundError


MAX_PDF_PAGES = 40
MAX_OCR_PAGES = 20
OCR_PAGE_TIMEOUT_SECONDS = 20
MIN_PAGE_TEXT_CHARACTERS = 40


class OcrUnavailableError(RuntimeError):
    pass


class PdfTextExtractionError(RuntimeError):
    pass


def _configure_tesseract() -> None:
    configured_path = os.getenv("TESSERACT_PATH", "").strip()
    if configured_path:
        executable = Path(configured_path)
        if not executable.is_file():
            raise OcrUnavailableError(
                "TESSERACT_PATH does not point to an installed tesseract.exe."
            )
        pytesseract.pytesseract.tesseract_cmd = str(executable)
        return
    executable_on_path = shutil.which("tesseract")
    if executable_on_path:
        pytesseract.pytesseract.tesseract_cmd = executable_on_path
        return
    if os.name == "nt":
        standard_install = Path("C:\\Program Files\\Tesseract-OCR\\tesseract.exe")
        if standard_install.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(standard_install)
            return
    raise OcrUnavailableError(
        "OCR is required for scanned papers. Install Tesseract OCR and add it to PATH, "
        "or set TESSERACT_PATH in backend/.env to the full path of tesseract.exe."
    )


def _poppler_path() -> str | None:
    configured_path = os.getenv("POPPLER_PATH", "").strip()
    if configured_path:
        poppler_directory = Path(configured_path)
        if not poppler_directory.is_dir():
            raise OcrUnavailableError("POPPLER_PATH must point to the Poppler bin directory.")
        return str(poppler_directory)
    executable = shutil.which("pdftoppm")
    if executable:
        return str(Path(executable).parent)
    if os.name == "nt":
        winget_packages = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        for poppler_directory in winget_packages.glob("oschwartz10612.Poppler*/poppler-*/Library/bin"):
            if (poppler_directory / "pdftoppm.exe").is_file():
                return str(poppler_directory)
    return None


def extract_pdf_text(pdf_source: Path | bytes, max_characters: int) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_source) if isinstance(pdf_source, bytes) else str(pdf_source))
    except (PdfReadError, OSError, ValueError) as error:
        raise PdfTextExtractionError("The PDF could not be opened.") from error

    text_parts = []
    remaining_chars = max_characters
    ocr_pages = 0
    poppler_path = None
    for page_index in range(min(len(reader.pages), MAX_PDF_PAGES)):
        if remaining_chars <= 0:
            break
        page_text = reader.pages[page_index].extract_text() or ""
        if len(page_text.strip()) < MIN_PAGE_TEXT_CHARACTERS:
            if ocr_pages >= MAX_OCR_PAGES:
                raise PdfTextExtractionError(
                    f"OCR is limited to {MAX_OCR_PAGES} scanned pages per paper."
                )
            if ocr_pages == 0:
                _configure_tesseract()
                poppler_path = _poppler_path()
                if not poppler_path:
                    raise OcrUnavailableError(
                        "Poppler is required to render scanned PDF pages. Install Poppler and add pdftoppm to PATH, "
                        "or set POPPLER_PATH in backend/.env to its bin directory."
                    )
            try:
                conversion_options = {
                    "dpi": 200,
                    "first_page": page_index + 1,
                    "last_page": page_index + 1,
                    "poppler_path": poppler_path,
                    "timeout": OCR_PAGE_TIMEOUT_SECONDS,
                }
                images = (
                    convert_from_bytes(pdf_source, **conversion_options)
                    if isinstance(pdf_source, bytes)
                    else convert_from_path(str(pdf_source), **conversion_options)
                )
                if not images:
                    raise PdfTextExtractionError(f"Could not render PDF page {page_index + 1} for OCR.")
                image: Image.Image = images[0]
                try:
                    page_text = pytesseract.image_to_string(
                        image,
                        config="--psm 6",
                        timeout=OCR_PAGE_TIMEOUT_SECONDS,
                    )
                finally:
                    image.close()
                ocr_pages += 1
            except (TesseractNotFoundError, RuntimeError, OSError) as error:
                raise PdfTextExtractionError(
                    f"OCR failed on page {page_index + 1}: {error}"
                ) from error
            except (PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError, PDFSyntaxError) as error:
                raise PdfTextExtractionError(
                    f"Could not render PDF page {page_index + 1} for OCR: {error}"
                ) from error

        page_text = page_text.strip()
        if page_text:
            excerpt = page_text[:remaining_chars]
            text_parts.append(excerpt)
            remaining_chars -= len(excerpt)
    return "\n".join(text_parts).strip()
