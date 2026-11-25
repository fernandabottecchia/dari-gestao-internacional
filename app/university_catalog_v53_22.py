"""Canonical institution labels, preserving original source evidence and IDs."""
import json
from pathlib import Path
from .university_reference_v53_20 import key
_DATA=json.loads(Path(__file__).with_name('university_catalog_v53_22.json').read_text(encoding='utf-8'))
def canonical_label(value):
    if not isinstance(value,str):return value
    legacy = _DATA['labels'].get(key(value),value)
    from .database import SessionLocal
    from .catalog_live_v53_28 import university_label
    from sqlalchemy.exc import OperationalError
    try:
        with SessionLocal() as db:
            current=university_label(db,value)
            return current if current!=value else university_label(db,legacy)
    except OperationalError:
        return legacy
