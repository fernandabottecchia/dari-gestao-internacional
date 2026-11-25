"""Hosted documents are stored directly in the same PostgreSQL database."""
def synchronize(db, *, force=False):
    return {'state': 'sincronizado', 'pending': 0, 'sent': 0, 'received': 0}
