"""Sources and conservative 2026+ tab rules for DARI.

Metadata is from read-only inspection of the nine Google Sheets supplied by DARI.
No personal response data is bundled. Dates <2026 in generic response tabs are skipped.
"""
import re
from datetime import datetime

OUTGOING_SOURCES = ()  # Existing sources arrive via the private database migration.
# Match standalone 2026/1, 2026-2, 2026.2, abbreviated 26-2, or a programme year
# such as 'Poli pelo Mundo 2026'. Reject accidental matches in numbers longer
# than four digits. Does not automatically pull administration tabs or old years.
YEAR_RE = re.compile(r'(?<!\d)(20(?:2[6-9]|[3-9]\d)|(?:2[6-9]))(?!\d)')


def year_of_tab(name):
    found = YEAR_RE.search(name or '')
    if not found: return None
    year = int(found.group(1))
    return 2000 + year if year < 100 else year


def eligible_tab(name, min_year=2026):
    return (year_of_tab(name) or 0) >= min_year


def generic_responses_tab(name):
    return bool(re.match(r'^respostas ao formul[aá]rio\s+\d+$', (name or '').strip(), re.I))


def date_is_recent(date_str, parse_timestamp, min_year=2026):
    value = parse_timestamp(date_str)
    return bool(value and value.year >= min_year)


def unique_tabs(configured, discovered):
    """Authorized discovery adds future year tabs; never adds old or helper tabs."""
    by_gid = {}
    for title,gid in list(configured or []) + list(discovered or []):
        if eligible_tab(title):
            by_gid[str(gid)] = (str(title), str(gid), False)
    # Raw response tabs are read with timestamp filtering, unless they are
    # already included via the same gid. This catches submissions that have
    # not yet been copied to a semester-specific tab.
    for title,gid in list(configured or []) + list(discovered or []):
        if generic_responses_tab(title):
            by_gid[str(gid)] = (str(title), str(gid), True)
    return sorted(by_gid.values(), key=lambda x:(x[2],x[0],x[1]))
