"""DARI v53.47: repositorio de documentos persistido em SQLite/PostgreSQL.

O SQLite continua fonte local durante a transicao. Alteracoes locais sao marcadas
pendentes; o cliente central sincroniza quando a API central estiver acessivel.
Arquivos antigos em disco sao importados uma unica vez, sem exclusao.
"""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Boolean, Column, Index, Integer, LargeBinary, String, Text, select, func
from sqlalchemy.orm import Session, load_only

from .database import Base

MAX_FILE_BYTES = int(os.getenv('DARI_DOC_MAX_BYTES', str(30 * 1024 * 1024)))
AREAS = frozenset(('incoming', 'outgoing', 'acordos'))


class StoredDocument(Base):
    __tablename__ = 'dari_document_items_v53_47'

    id = Column(String(32), primary_key=True)
    area = Column(String(16), nullable=False)
    path = Column(String(1024), nullable=False)
    kind = Column(String(8), nullable=False)  # folder | file
    body = Column(LargeBinary, nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=True)
    modified_at = Column(String(32), nullable=False)
    change_id = Column(String(32), nullable=False)
    synced_change_id = Column(String(32), nullable=True)
    deleted = Column(Boolean, nullable=False, default=False)


Index('ix_dari_document_items_v53_47_area_path', StoredDocument.area, StoredDocument.path)


class DocumentMigration(Base):
    __tablename__ = 'dari_document_meta_v53_47'
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False, default='')


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def validate_path(area: str, path: str = '', *, allow_root: bool = True) -> str:
    if area not in AREAS:
        raise HTTPException(404, 'Area documental invalida.')
    raw = (path or '').strip().replace('\\', '/')
    if raw.startswith('/') or '\x00' in raw or len(raw) > 1024 or ':' in raw:
        raise HTTPException(400, 'Caminho documental invalido.')
    if not raw:
        if allow_root: return ''
        raise HTTPException(400, 'Caminho vazio.')
    parts = raw.split('/')
    if any(p in ('', '.', '..') or p.startswith('.dari_') for p in parts):
        raise HTTPException(400, 'Caminho documental invalido.')
    return '/'.join(parts)


def clean_name(name: str) -> str:
    name = (name or '').strip().replace('\x00', '')
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, '_')
    name = name.rstrip(' .')
    if not name or name in ('.', '..') or name.startswith('.dari_') or len(name) > 180:
        raise HTTPException(400, 'Nome documental invalido.')
    return name


def _touch(row: StoredDocument):
    row.modified_at = now_iso()
    row.change_id = uuid4().hex


def _active(db: Session, area: str, path: str):
    return db.scalar(select(StoredDocument).where(
        StoredDocument.area == area, StoredDocument.path == path,
        StoredDocument.deleted.is_(False)).limit(1))


def _unique_path(db: Session, area: str, path: str):
    if not _active(db, area, path): return path
    original = Path(path)
    parent = original.parent.as_posix()
    parent = '' if parent == '.' else parent + '/'
    stem, suffix = original.stem, original.suffix
    for n in range(2, 10001):
        candidate = parent + f'{stem} ({n}){suffix}'
        if not _active(db, area, candidate): return candidate
    raise HTTPException(409, 'Muitos arquivos com este nome.')


def get(db: Session, area: str, path: str) -> StoredDocument | None:
    return _active(db, area, validate_path(area, path, allow_root=False))


def get_id(db: Session, doc_id: str) -> StoredDocument | None:
    return db.get(StoredDocument, doc_id)


def _require_parent(db: Session, area: str, parent: str):
    parent = validate_path(area, parent)
    if parent:
        item = _active(db, area, parent)
        if not item or item.kind != 'folder':
            raise HTTPException(404, 'Pasta nao encontrada.')
    return parent


def list_children(db: Session, area: str, parent: str = '') -> list[StoredDocument]:
    parent = _require_parent(db, area, parent)
    prefix = parent + '/' if parent else ''
    rows = db.scalars(select(StoredDocument).options(load_only(
        StoredDocument.id, StoredDocument.area, StoredDocument.path,
        StoredDocument.kind, StoredDocument.modified_at, StoredDocument.deleted,
    )).where(
        StoredDocument.area == area, StoredDocument.deleted.is_(False),
        StoredDocument.path.like(prefix.replace('%', '\\%').replace('_', '\\_') + '%', escape='\\'))).all()
    # Delimitar pelos componentes e nao pelos caracteres especiais de LIKE.
    return sorted((r for r in rows if r.path.startswith(prefix) and '/' not in r.path[len(prefix):]),
                  key=lambda r: (r.kind != 'folder', r.path.casefold()))


def create_folder(db: Session, area: str, parent: str, name: str) -> StoredDocument:
    parent = _require_parent(db, area, parent)
    path = validate_path(area, parent + '/' + clean_name(name) if parent else clean_name(name), allow_root=False)
    if _active(db, area, path): raise HTTPException(409, 'Ja existe um item com este nome.')
    r = StoredDocument(id=uuid4().hex, area=area, path=path, kind='folder', body=None,
                       size_bytes=0, sha256=None, synced_change_id=None, deleted=False)
    _touch(r)
    db.add(r);db.commit()
    return r


def create_file(db: Session, area: str, parent: str, name: str, data: bytes) -> StoredDocument:
    parent = _require_parent(db, area, parent)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, f'Arquivo excede o limite de {MAX_FILE_BYTES // (1024 * 1024)} MB.')
    name = clean_name(name)
    path = _unique_path(db, area, parent + '/' + name if parent else name)
    if len(path) > 1024: raise HTTPException(400, 'Caminho muito longo.')
    r = StoredDocument(id=uuid4().hex, area=area, path=path, kind='file', body=data,
                       size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                       synced_change_id=None, deleted=False)
    _touch(r)
    db.add(r);db.commit()
    return r


def _subtree(db: Session, row: StoredDocument) -> list[StoredDocument]:
    return [r for r in db.scalars(select(StoredDocument).where(
        StoredDocument.area == row.area, StoredDocument.deleted.is_(False))).all()
        if r.path == row.path or r.path.startswith(row.path + '/')]


def rename(db: Session, area: str, path: str, new_name: str) -> StoredDocument:
    path = validate_path(area, path, allow_root=False)
    row = _active(db, area, path)
    if row is None: raise HTTPException(404, 'Item nao encontrado.')
    parent = path.rsplit('/', 1)[0] if '/' in path else ''
    name = clean_name(new_name)
    new_path = parent + '/' + name if parent else name
    if new_path == path: return row
    subtree = _subtree(db, row)
    old_paths = {r.path for r in subtree}
    dest_paths = {new_path + r.path[len(path):] for r in subtree}
    if any(len(v) > 1024 for v in dest_paths): raise HTTPException(400, 'Caminho muito longo.')
    # Nao permitir que a operacao sobreponha outros cadastros.
    for proposed in dest_paths:
        occupant = _active(db, area, proposed)
        if occupant and occupant.path not in old_paths:
            raise HTTPException(409, 'Ja existe um item com este nome.')
    for item in subtree:
        item.path = new_path + item.path[len(path):]
        _touch(item)
    db.commit()
    return row


def delete(db: Session, area: str, path: str):
    row = get(db, area, path)
    if not row: raise HTTPException(404, 'Item nao encontrado.')
    for child in _subtree(db, row):
        child.deleted = True
        child.body = None
        _touch(child)
    db.commit()


def pending_count(db: Session) -> int:
    return db.scalar(select(func.count(StoredDocument.id)).where(
        StoredDocument.synced_change_id.is_distinct_from(StoredDocument.change_id))) or 0


def _import_item(db: Session, area: str, path: str, kind: str, data: bytes | None):
    if _active(db, area, path): return False
    row = StoredDocument(id=uuid4().hex, area=area, path=path, kind=kind,
                         body=data if kind=='file' else None,
                         size_bytes=len(data or b'') if kind=='file' else 0,
                         sha256=hashlib.sha256(data or b'').hexdigest() if kind=='file' else None,
                         deleted=False, synced_change_id=None)
    _touch(row);db.add(row)
    return True


def import_legacy_once(db: Session, file_root: Path) -> dict:
    """Importacao nao destrutiva das pastas antigas, uma vez por banco."""
    marker = 'legacy_filesystem_import_v53_47'
    if db.get(DocumentMigration, marker): return {'already_done': True}
    imported, skipped = 0, []
    for area in AREAS:
        area_root = file_root / area
        if not area_root.exists(): continue
        for item in sorted(area_root.rglob('*'), key=lambda p: (len(p.parts), str(p))):
            if item.is_symlink() or any(part.startswith('.dari_') for part in item.relative_to(area_root).parts): continue
            if not item.is_file() and not item.is_dir(): continue
            rel = item.relative_to(area_root).as_posix()
            try:
                path = validate_path(area, rel, allow_root=False)
                if item.is_dir():
                    if _import_item(db, area, path, 'folder', None): imported += 1
                else:
                    if item.stat().st_size > MAX_FILE_BYTES:
                        skipped.append(f'{area}/{rel}: acima do limite');continue
                    if _import_item(db, area, path, 'file', item.read_bytes()): imported += 1
            except (OSError, HTTPException) as exc:
                skipped.append(f'{area}/{rel}: {exc}')
    if not skipped:
        db.add(DocumentMigration(key=marker, value=f'importados={imported}; ignorados=0'))
    db.commit()
    return {'imported': imported, 'skipped': skipped}


def to_wire(row: StoredDocument, *, with_body: bool = False) -> dict:
    value = {k: getattr(row, k) for k in (
        'id', 'area', 'path', 'kind', 'size_bytes', 'sha256', 'modified_at', 'change_id', 'deleted')}
    if with_body:
        value['body_b64'] = base64.b64encode(row.body or b'').decode('ascii') if row.kind == 'file' and not row.deleted else None
    return value


def from_wire(db: Session, payload: dict, *, mark_synced: bool = False):
    try:
        doc_id = payload['id']
        if len(doc_id) != 32 or any(c not in '0123456789abcdef' for c in doc_id): raise ValueError('id')
        area = payload['area']
        path = validate_path(area, payload['path'], allow_root=False)
        kind = payload['kind']
        if kind not in ('file','folder'):raise ValueError('kind')
        deleted = bool(payload['deleted'])
        content = base64.b64decode(payload['body_b64'], validate=True) if kind=='file' and not deleted else None
        if content is not None and len(content)>MAX_FILE_BYTES:raise ValueError('size')
        if kind=='file' and not deleted:
            if content is None:raise ValueError('missing content')
            digest = hashlib.sha256(content).hexdigest()
            if digest != payload['sha256']:raise ValueError('sha256')
            if len(content) != int(payload['size_bytes']):raise ValueError('size_bytes')
        change_id = payload['change_id']
        if len(change_id) != 32 or any(c not in '0123456789abcdef' for c in change_id):raise ValueError('change_id')
        modified_at = str(payload['modified_at'])[:32]
        datetime.fromisoformat(modified_at)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(422, f'Documento malformado: {exc}') from exc
    item = db.get(StoredDocument, doc_id)
    if item is None:
        item = StoredDocument(id=doc_id)
        db.add(item)
    item.area=area;item.path=path;item.kind=kind;item.deleted=deleted
    item.body=content;item.size_bytes=len(content or b'') if kind=='file' and not deleted else 0
    item.sha256=hashlib.sha256(content).hexdigest() if content is not None else None
    item.modified_at=modified_at;item.change_id=change_id
    if mark_synced: item.synced_change_id = change_id
    db.commit()
    return item
