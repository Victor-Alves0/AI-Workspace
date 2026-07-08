"""Extração de texto de anexos (integração "Extração de Texto").

Suporta PDF, DOCX, XLSX/XLSM, PPTX e CSV. O foco é EFICIÊNCIA de tokens: cada
formato tem limites configuráveis (páginas, linhas, caracteres) e o texto é
enxugado (espaços/linhas em branco colapsados) antes de ir ao modelo.

A config vem do perfil do usuário (`profile.text_extraction`); `DEFAULTS` cobre
usuários sem config. Tudo degrada com segurança: falha de parsing vira
ExtractionError (mostrada como aviso), nunca derruba o turno.
"""

from __future__ import annotations

import csv as _csv
import io
import logging
import re

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    pass


# defaults conservadores p/ conter o consumo de tokens
DEFAULTS: dict = {
    "pdf": True,
    "docx": True,
    "xlsx": True,
    "pptx": True,
    "csv": True,
    "max_chars": 20000,     # teto de caracteres por arquivo (corta o excesso)
    "pdf_max_pages": 30,    # páginas lidas de um PDF
    "xlsx_max_rows": 200,   # linhas lidas por planilha/CSV
    "collapse_whitespace": True,  # colapsa espaços/linhas em branco
    # OCR (imagens e PDFs escaneados)
    "ocr": True,            # liga o OCR (fallback qdo o PDF não tem texto; imagens)
    "ocr_engine": "tesseract",  # "tesseract" (local) | "vision" (modelo de visão)
    "ocr_lang": "por+eng",  # idiomas do tesseract
    "ocr_max_pages": 10,    # páginas rasterizadas p/ OCR (mais caro que texto puro)
}

# tipos de imagem que o OCR (tesseract) consegue ler
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif")

# extensões e mimes suportados por tipo
_KINDS = {
    "pdf": ((".pdf",), ("application/pdf",)),
    "docx": ((".docx",), ("wordprocessingml",)),
    "xlsx": ((".xlsx", ".xlsm"), ("spreadsheetml",)),
    "pptx": ((".pptx",), ("presentationml",)),
    "csv": ((".csv",), ("text/csv",)),
}


def default_config() -> dict:
    return dict(DEFAULTS)


def _cfg(config: dict | None, key: str):
    if config and key in config and config[key] is not None:
        return config[key]
    return DEFAULTS.get(key)


def kind_of(filename: str | None, mime: str | None) -> str | None:
    name = (filename or "").lower()
    m = (mime or "").lower()
    for kind, (exts, mimes) in _KINDS.items():
        if name.endswith(exts) or any(x in m for x in mimes):
            return kind
    return None


def is_extractable(filename: str | None, mime: str | None) -> bool:
    return kind_of(filename, mime) is not None


def _collapse(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ocr_available() -> bool:
    """Se o tesseract (binário) está acessível via pytesseract."""
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:  # noqa: BLE001
        return False


def ocr_image_bytes(data: bytes, lang: str = "por+eng") -> str:
    """OCR local (tesseract) sobre os bytes de UMA imagem. Levanta ExtractionError."""
    try:
        import pytesseract
        from PIL import Image
    except Exception as exc:  # noqa: BLE001 - libs/binário ausentes
        raise ExtractionError(f"OCR indisponível ({exc})")
    try:
        img = Image.open(io.BytesIO(data))
        return pytesseract.image_to_string(img, lang=lang) or ""
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"falha no OCR ({type(exc).__name__})")


def extract(filename: str | None, mime: str | None, data: bytes, config: dict | None = None) -> str:
    """Extrai texto do arquivo respeitando a config. Levanta ExtractionError."""
    kind = kind_of(filename, mime)
    if kind is None:
        raise ExtractionError("formato não suportado para extração")
    if not _cfg(config, kind):
        raise ExtractionError(f"extração de {kind.upper()} está desativada")
    try:
        if kind == "pdf":
            text = _pdf(
                data,
                int(_cfg(config, "pdf_max_pages") or 30),
                ocr=bool(_cfg(config, "ocr")),
                ocr_lang=str(_cfg(config, "ocr_lang") or "por+eng"),
                ocr_max_pages=int(_cfg(config, "ocr_max_pages") or 10),
            )
        elif kind == "docx":
            text = _docx(data)
        elif kind == "xlsx":
            text = _xlsx(data, int(_cfg(config, "xlsx_max_rows") or 200))
        elif kind == "pptx":
            text = _pptx(data)
        else:  # csv
            text = _csv_text(data, int(_cfg(config, "xlsx_max_rows") or 200))
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - parsing de arquivo arbitrário
        logger.warning("Falha ao extrair '%s' (%s)", filename, exc)
        raise ExtractionError(f"não foi possível ler o arquivo ({type(exc).__name__})")

    if _cfg(config, "collapse_whitespace"):
        text = _collapse(text)
    max_chars = int(_cfg(config, "max_chars") or 0)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n…(conteúdo truncado)"
    return text


# --------------------------------------------------------------------------- #
# parsers por formato (imports preguiçosos p/ falhar só se o formato for usado)
# --------------------------------------------------------------------------- #
def _pdf(data: bytes, max_pages: int, ocr: bool = True, ocr_lang: str = "por+eng", ocr_max_pages: int = 10) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages
    out: list[str] = []
    for i, page in enumerate(pages):
        if i >= max_pages:
            out.append(f"\n…(restam {len(pages) - max_pages} páginas não lidas)")
            break
        out.append(page.extract_text() or "")
    text = "\n\n".join(p for p in out if p)

    # PDF escaneado (pouco ou nenhum texto selecionável): cai p/ OCR das páginas
    if ocr and len(text.strip()) < 40:
        ocr_text = _pdf_ocr(data, ocr_lang, min(ocr_max_pages, max_pages))
        if ocr_text.strip():
            return ocr_text
    return text


def _pdf_ocr(data: bytes, lang: str, max_pages: int) -> str:
    """Rasteriza as páginas (poppler) e roda OCR (tesseract) — PDFs escaneados."""
    try:
        from pdf2image import convert_from_bytes
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"OCR de PDF indisponível ({exc})")
    images = convert_from_bytes(data, dpi=200, first_page=1, last_page=max(1, max_pages))
    out: list[str] = []
    for i, img in enumerate(images, 1):
        import io as _io

        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        page_text = ocr_image_bytes(buf.getvalue(), lang)
        if page_text.strip():
            out.append(f"[Página {i} (OCR)]\n{page_text.strip()}")
    return "\n\n".join(out)


def _docx(data: bytes) -> str:
    import docx  # python-docx

    doc = docx.Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _xlsx(data: bytes, max_rows: int) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[str] = []
    try:
        for ws in wb.worksheets:
            out.append(f"## Planilha: {ws.title}")
            n = 0
            for row in ws.iter_rows(values_only=True):
                if n >= max_rows:
                    out.append("…(demais linhas omitidas)")
                    break
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    out.append(" | ".join(cells))
                    n += 1
    finally:
        wb.close()
    return "\n".join(out)


def _pptx(data: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    out: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        out.append(f"## Slide {i}")
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                for para in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in para.runs)
                    if t.strip():
                        out.append(t)
    return "\n".join(out)


def _csv_text(data: bytes, max_rows: int) -> str:
    text = data.decode("utf-8", errors="replace")
    reader = _csv.reader(io.StringIO(text))
    out: list[str] = []
    for i, row in enumerate(reader):
        if i >= max_rows:
            out.append("…(demais linhas omitidas)")
            break
        out.append(" | ".join(row))
    return "\n".join(out)
