"""Human-friendly team IDs with a check character.

Format: PREFIX-XXX-XXX  (5 random characters + 1 check character).
The alphabet leaves out look-alikes (0/O, 1/I/L) so IDs survive being read aloud or
copied from a screenshot, and the check character catches single typos and swaps.
"""
import re
import secrets

from django.conf import settings

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 31 symbols (prime modulus)
BODY_LEN = 5
_INDEX = {c: i for i, c in enumerate(ALPHABET)}


def _check_char(body):
    total = sum((pos + 1) * _INDEX[c] for pos, c in enumerate(body))
    return ALPHABET[total % len(ALPHABET)]


def generate():
    body = "".join(secrets.choice(ALPHABET) for _ in range(BODY_LEN))
    code = body + _check_char(body)
    return f"{settings.TEAM_ID_PREFIX}-{code[:3]}-{code[3:]}"


def normalize(raw):
    """Return the canonical ID for user input, or None if it is malformed / fails the check."""
    if not raw:
        return None
    text = re.sub(r"[\s\-_]", "", raw.upper())
    prefix = settings.TEAM_ID_PREFIX.upper()
    if text.startswith(prefix):
        text = text[len(prefix):]
    if len(text) != BODY_LEN + 1 or any(c not in _INDEX for c in text):
        return None
    if _check_char(text[:BODY_LEN]) != text[-1]:
        return None
    return f"{settings.TEAM_ID_PREFIX}-{text[:3]}-{text[3:]}"


def generate_pin():
    """6-digit PIN used to protect edits to a team."""
    return f"{secrets.randbelow(10**6):06d}"
