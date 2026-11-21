import os
import re
import zipfile

from django.core.exceptions import ValidationError
from django.conf import settings

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
MAX_UNZIPPED_MB = 200          # reject zip bombs: total uncompressed size claimed by a .docx
MAX_ZIP_ENTRIES = 2000
_PDF_ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia|XFA)(?![A-Za-z])")
_DOCX_BAD_PARTS = ("vbaproject", "activex", "/embeddings/", "oleobject")
_DOCX_BAD_SUFFIX = (".exe", ".dll", ".js", ".vbs", ".bat", ".cmd", ".scr", ".ps1", ".com", ".msi", ".jar", ".lnk")
_OLE_MACRO_MARKERS = ("_VBA_PROJECT", "Macros")  # stream names inside legacy .doc files that hold macros
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .doc


def normalize_enrolment(value):
    return re.sub(r"\s+", "", (value or "")).upper()


def validate_enrolment(value):
    if not re.match(settings.ENROLMENT_REGEX, value):
        raise ValidationError("Enter a valid enrolment number (letters and digits only, no spaces).")


def normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def validate_phone(value):
    if not re.fullmatch(r"[6-9]\d{9}", value):
        raise ValidationError("Enter a valid 10-digit mobile number.")


def validate_email_domain(value):
    allowed = settings.ALLOWED_EMAIL_DOMAINS
    if allowed and value.rsplit("@", 1)[-1].lower() not in allowed:
        raise ValidationError("Use your institute email address (" + ", ".join("@" + d for d in allowed) + ").")


def validate_report_file(uploaded, max_mb):
    """Check extension, size and real file content. Returns the lower-case extension."""
    ext = os.path.splitext(uploaded.name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError("Only PDF or Word files (.pdf, .doc, .docx) are accepted.")
    if uploaded.size == 0:
        raise ValidationError("That file is empty.")
    if uploaded.size > max_mb * 1024 * 1024:
        raise ValidationError(f"The file is larger than the {max_mb} MB limit.")

    uploaded.seek(0)
    head = uploaded.read(2048)
    uploaded.seek(0)

    if ext == ".pdf":
        if not head.startswith(b"%PDF-") and b"%PDF-" not in head[:1024]:
            raise ValidationError("This does not look like a valid PDF file.")
        uploaded.seek(0)
        data = uploaded.read()
        uploaded.seek(0)
        if _PDF_ACTIVE.search(data):
            raise ValidationError("PDFs containing scripts, launch actions or embedded files are not accepted. Export a plain PDF.")
        return ext

    is_ole = head.startswith(_OLE_MAGIC)
    is_zip = head.startswith(b"PK")
    if ext == ".doc" and is_ole:
        uploaded.seek(0)
        data = uploaded.read()
        uploaded.seek(0)
        if any(m.encode("utf-16-le") in data or m.encode("ascii") in data for m in _OLE_MACRO_MARKERS):
            raise ValidationError("Word files containing macros are not accepted. Save as .docx (without macros) or export a PDF.")
        return ext
    if is_zip:  # .docx (also tolerates a .docx saved with a .doc extension)
        try:
            with zipfile.ZipFile(uploaded) as zf:
                infos = zf.infolist()
                names = {i.filename for i in infos}
                if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNZIPPED_MB * 1024 * 1024:
                    raise ValidationError("This Word file is not accepted (unusually large when unpacked).")
                lowered = [n.lower() for n in names]
                if any(bad in n for n in lowered for bad in _DOCX_BAD_PARTS) or any(n.endswith(_DOCX_BAD_SUFFIX) for n in lowered):
                    raise ValidationError("Word files with macros, embedded objects or programs are not accepted. Export a PDF or save a plain .docx.")
                if "[Content_Types].xml" in names and b"macroEnabled" in zf.read("[Content_Types].xml"):
                    raise ValidationError("Macro-enabled Word files are not accepted.")
        except zipfile.BadZipFile:
            raise ValidationError("This Word file appears to be corrupted.")
        finally:
            uploaded.seek(0)
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            return ext
    raise ValidationError("This does not look like a valid Word document.")
