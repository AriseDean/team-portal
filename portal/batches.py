"""Parse batch ranges such as "B1-B16, B65-B80, A3" into batch names."""
import re

MAX_BATCHES = 500
_TOKEN = re.compile(r"^([A-Za-z]+)\s*(\d+)(?:\s*(?:-|to)\s*(?:\1)?\s*(\d+))?$", re.IGNORECASE)


def expand(text):
    """Return an ordered, de-duplicated list of batch names. Raises ValueError with a readable message."""
    names, seen = [], set()
    for raw in re.split(r"[,\n;]+", text or ""):
        token = raw.strip()
        if not token:
            continue
        m = _TOKEN.match(token)
        if not m:
            raise ValueError(f"Can't read \u201c{token}\u201d. Use forms like B1-B16 or B7.")
        prefix = m.group(1).upper()
        first = int(m.group(2))
        last = int(m.group(3)) if m.group(3) else first
        if last < first:
            raise ValueError(f"\u201c{token}\u201d runs backwards.")
        for n in range(first, last + 1):
            name = f"{prefix}{n}"
            if name not in seen:
                seen.add(name)
                names.append(name)
        if len(names) > MAX_BATCHES:
            raise ValueError(f"That is more than {MAX_BATCHES} batches at once.")
    if not names:
        raise ValueError("Enter at least one batch, e.g. B1-B16, B65-B80.")
    return names
