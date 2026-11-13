"""
Document Reader
================
Extracts plain text/paragraphs from whatever a student uploads
(.docx / .pdf / .pptx / .txt) into one common shape — a list of
non-empty paragraph strings — so every downstream feature (viewing,
summarizing, the AI Assistant, and the quiz generator) can work off a
single, consistent representation regardless of source file type.
"""

from docx import Document as DocxDocument
from pypdf import PdfReader
from pptx import Presentation


def extract_paragraphs(filepath, file_type):
    file_type = (file_type or "").lower()
    if file_type == "docx":
        return _extract_docx(filepath)
    elif file_type == "pdf":
        return _extract_pdf(filepath)
    elif file_type == "pptx":
        return _extract_pptx(filepath)
    elif file_type == "txt":
        return _extract_txt(filepath)
    return []


def _extract_docx(filepath):
    doc = DocxDocument(filepath)
    return [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]


def _extract_pdf(filepath):
    paragraphs = []
    try:
        reader = PdfReader(filepath)
        for page in reader.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                line = line.strip()
                if line:
                    paragraphs.append(line)
    except Exception:
        pass
    return paragraphs


def _extract_pptx(filepath):
    paragraphs = []
    try:
        prs = Presentation(filepath)
        for i, slide in enumerate(prs.slides, start=1):
            slide_lines = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = "".join(run.text for run in para.runs).strip()
                        if not text and para.text:
                            text = para.text.strip()
                        if text:
                            slide_lines.append(text)
            if slide_lines:
                paragraphs.append(f"Slide {i}: " + slide_lines[0])
                paragraphs.extend(slide_lines[1:])
    except Exception:
        pass
    return paragraphs


def _extract_txt(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except OSError:
        return []
    paragraphs = [line.strip() for line in raw.split("\n") if line.strip()]
    return paragraphs


def extract_pptx_slides(filepath):
    """
    Structured slide data for the fallback "slide deck" viewer (used when
    LibreOffice isn't installed and true PDF conversion isn't possible).
    Returns {"aspect_ratio": w/h, "slides": [{"title": ..., "bullets": [...]}]}.
    """
    result = {"aspect_ratio": 16 / 9, "slides": []}
    try:
        prs = Presentation(filepath)
        if prs.slide_width and prs.slide_height:
            result["aspect_ratio"] = prs.slide_width / prs.slide_height

        for slide in prs.slides:
            title = ""
            bullets = []
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                is_title = (getattr(shape, "shape_type", None) is not None
                            and shape == getattr(slide.shapes, "title", None))
                for para in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in para.runs).strip() or para.text.strip()
                    if not text:
                        continue
                    if is_title and not title:
                        title = text
                    else:
                        bullets.append(text)
            result["slides"].append({"title": title or "Untitled slide", "bullets": bullets})
    except Exception:
        pass
    return result


def paragraphs_to_text(paragraphs):
    return "\n".join(paragraphs)


def text_to_paragraphs(text):
    return [line.strip() for line in (text or "").split("\n") if line.strip()]
