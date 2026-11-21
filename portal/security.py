from django.conf import settings
from django.core.cache import cache


def client_ip(request):
    """Best-effort client IP. X-Forwarded-For is only honoured when TRUST_PROXY=1."""
    if settings.TRUST_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[-1].strip()  # last hop = the one our own proxy appended
    return request.META.get("REMOTE_ADDR") or None


def hit(key, window):
    """Count one event under `key` within a rolling `window` (seconds); returns the new count."""
    key = f"rl:{key}"
    if cache.add(key, 1, window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return 1


def count(key):
    return cache.get(f"rl:{key}", 0)


def clear(key):
    cache.delete(f"rl:{key}")
