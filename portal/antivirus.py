"""Optional virus scan of every upload through a ClamAV daemon (clamd). Off unless CLAMAV_HOST is set."""
import logging
import socket
import struct

from django.conf import settings
from django.core.exceptions import ValidationError

log = logging.getLogger(__name__)


def _instream(uploaded, host, port, timeout):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(b"zINSTREAM\0")
        uploaded.seek(0)
        for chunk in uploaded.chunks(64 * 1024):
            sock.sendall(struct.pack("!I", len(chunk)) + chunk)
        sock.sendall(struct.pack("!I", 0))
        reply = b""
        while not reply.endswith(b"\0") and len(reply) < 4096:
            part = sock.recv(4096)
            if not part:
                break
            reply += part
    uploaded.seek(0)
    return reply.rstrip(b"\0").decode("utf-8", "replace").strip()


def scan(uploaded):
    """Raise ValidationError if the file is infected (or, when CLAMAV_REQUIRED, if it can't be scanned)."""
    host = settings.CLAMAV_HOST
    if not host:
        return
    try:
        result = _instream(uploaded, host, settings.CLAMAV_PORT, settings.CLAMAV_TIMEOUT)
    except OSError:
        log.exception("virus scanner unreachable")
        if settings.CLAMAV_REQUIRED:
            raise ValidationError("The virus scanner is temporarily unavailable. Please try again in a few minutes.")
        return
    if result.endswith("FOUND"):
        log.warning("upload rejected by scanner: %s", result)
        raise ValidationError("This file was flagged by the virus scanner and was not accepted.")
    if not result.endswith("OK"):
        log.error("unexpected scanner reply: %r", result)
        if settings.CLAMAV_REQUIRED:
            raise ValidationError("The virus scanner returned an unexpected answer. Please try again shortly.")
