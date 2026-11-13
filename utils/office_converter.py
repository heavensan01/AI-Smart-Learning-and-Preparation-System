"""
Office Converter
=================
Converts DOCX/PPTX to PDF using headless LibreOffice, when it's installed,
so the Study Materials viewer can show the document exactly as uploaded —
real fonts, real layout, real A4 pagination — via the same native browser
PDF viewer already used for .pdf uploads.

This is entirely optional. If `soffice` / `libreoffice` isn't found on the
system (very common on a student's Windows laptop without LibreOffice
installed), `is_available()` returns False and the app automatically shows
a different, still-readable fallback view instead (see material_view route
in app.py) — nothing breaks either way.
"""

import os
import shutil
import subprocess
import tempfile
import uuid

_SOFFICE_BIN = None
_CHECKED = False


def _find_soffice():
    global _SOFFICE_BIN, _CHECKED
    if _CHECKED:
        return _SOFFICE_BIN
    _CHECKED = True
    for candidate in ("soffice", "libreoffice"):
        path = shutil.which(candidate)
        if path:
            _SOFFICE_BIN = path
            break
    return _SOFFICE_BIN


def is_available():
    return _find_soffice() is not None


def convert_to_pdf(input_path, output_dir, timeout=45):
    """
    Converts input_path (.docx/.pptx) to a PDF inside output_dir.
    Returns the resulting PDF filepath, or None if conversion isn't
    possible (LibreOffice missing) or fails for any reason.
    """
    soffice = _find_soffice()
    if not soffice or not os.path.exists(input_path):
        return None

    os.makedirs(output_dir, exist_ok=True)
    # Isolated profile dir per conversion avoids lock-file conflicts when
    # multiple students convert documents around the same time.
    profile_dir = os.path.join(tempfile.gettempdir(), f"soffice_profile_{uuid.uuid4().hex}")

    try:
        subprocess.run(
            [
                soffice, "--headless", "--norestore",
                f"-env:UserInstallation=file://{profile_dir}",
                "--convert-to", "pdf", "--outdir", output_dir, input_path,
            ],
            capture_output=True, timeout=timeout, check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

    base = os.path.splitext(os.path.basename(input_path))[0]
    produced = os.path.join(output_dir, base + ".pdf")
    return produced if os.path.exists(produced) else None
