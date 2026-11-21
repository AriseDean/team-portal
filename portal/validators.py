import os
import re
import zipfile

from django.core.exceptions import ValidationError
from django.conf import settings

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
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
        if b"%PDF-" not in head[:1024]:
            raise ValidationError("This does not look like a valid PDF file.")
        return ext

    is_ole = head.startswith(_OLE_MAGIC)
    is_zip = head.startswith(b"PK")
    if ext == ".doc" and is_ole:
        return ext
    if is_zip:  # .docx (also tolerates a .docx saved with a .doc extension)
        try:
            with zipfile.ZipFile(uploaded) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile:
            raise ValidationError("This Word file appears to be corrupted.")
        finally:
            uploaded.seek(0)
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            return ext
    raise ValidationError("This does not look like a valid Word document.")
