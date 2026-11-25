"""DARI v53.19: arquivamento seguro de acordos vigentes oriundos da planilha.

O vencimento é verificado na consulta, não exige cron/job. Cria um snapshot
imutável para que dados de instrumentos arquivados não sejam perdidos com
sincronizações futuras. A tabela principal agreements permanece intacta.
"""
from __future__ import annotations

from datetime import date, datetime
from json import dumps, loads
import unicodedata
from sqlalchemy import text
from sqlalchemy.orm import Session

TABLE = 'dari_sheet_agreements_archived_v53_19'
SOURCE = 'controle_acordos_dari'


def _create_schema(db: Session) -> None:
    pass  # Installed by tools.migrate_sqlite, never by a web request.


def refresh_archive(db: Session, today: date | None = None) -> set[int]:
    """Grava somente acordos Ativo + fonte planilha + vencimento <= hoje.

    Não altera os acordos antigos do Access. Um acordo arquivado permanece
    arquivado mesmo se a planilha alterar posteriormente seu status/registro.
    """
    day = today or date.today()
    _create_schema(db)
    current = db.execute(text('''
        SELECT a.id, a.university_id, a.agreement_type, a.sig_number, a.sei_number,
               a.progress, a.ufrj_stage, a.signed_date, a.validity_years,
               a.expiry_date, a.status, a.ufrj_coordinator, a.notes,
               a.drive_folder_url, r.source_key
        FROM agreements a
        JOIN agreement_source_records r ON r.agreement_id = a.id
        JOIN integration_sources src ON src.id = r.source_id
        WHERE src.code = :source_code AND r.source_deleted = FALSE
          AND lower(trim(coalesce(a.status, ''))) = 'ativo'
          AND a.expiry_date IS NOT NULL AND a.expiry_date <= :today
          AND NOT EXISTS (
              SELECT 1 FROM dari_sheet_agreements_archived_v53_19 ar
              WHERE ar.agreement_id = a.id)
    '''), {'source_code': SOURCE, 'today': day.isoformat()}).mappings().all()
    for r in current:
        data = {key: (str(v) if isinstance(v, (date, datetime)) else v)
                for key, v in dict(r).items()}
        db.execute(text('''INSERT INTO dari_sheet_agreements_archived_v53_19
            (agreement_id, university_id, source_key, archived_at, expiry_date, snapshot_json)
            VALUES (:agreement_id, :university_id, :source_key, :archived_at,
                    :expiry_date, :snapshot_json)
            ON CONFLICT (agreement_id) DO NOTHING'''), {
                'agreement_id': r['id'], 'university_id': r['university_id'],
                'source_key': r['source_key'], 'archived_at': datetime.now().isoformat(),
                'expiry_date': str(r['expiry_date'])[:10],
                'snapshot_json': dumps(data, ensure_ascii=False),
            })
    if current:
        db.commit()
    else:
        # DDL que cria a tabela pode ter iniciado transação: manter o estado
        # de leitura estável, sem adicionar um commit se nada foi arquivado.
        db.flush()
    rows = db.execute(text('''SELECT agreement_id
        FROM dari_sheet_agreements_archived_v53_19''')).all()
    return {int(r[0]) for r in rows}


def _name_key(name: str) -> str:
    value = unicodedata.normalize('NFKD', (name or '').strip())
    return ' '.join(''.join(c for c in value if not unicodedata.combining(c)).casefold().split())


def _institution_ids(db: Session, university_id: int) -> set[int]:
    uni = db.execute(text('SELECT id,name,country_id FROM universities WHERE id=:id'), {'id': university_id}).mappings().first()
    if not uni:
        return set()
    all_unis = db.execute(text('SELECT id,name,country_id FROM universities')).mappings().all()
    return {int(u['id']) for u in all_unis if u['id'] == university_id
            or (u['country_id'] == uni['country_id'] and _name_key(u['name']) == _name_key(uni['name']))}


def _display(d: str | None) -> str:
    if not d:
        return '—'
    try:
        return date.fromisoformat(str(d)[:10]).strftime('%d/%m/%Y')
    except ValueError:
        return str(d)


def archive_history_rows(db: Session, university_id: int, today: date | None = None) -> list[dict]:
    refresh_archive(db, today=today)
    ids = _institution_ids(db, university_id)
    if not ids:
        return []
    # IDs são derivados de chave primária inteira do próprio banco.
    sql = 'SELECT * FROM dari_sheet_agreements_archived_v53_19 WHERE university_id IN (' + ','.join(str(int(i)) for i in sorted(ids)) + ') ORDER BY expiry_date DESC, agreement_id DESC'
    rows = db.execute(text(sql)).mappings().all()
    output=[]
    for item in rows:
        values=loads(item['snapshot_json'])
        output.append({
            'archived_sheet': True,
            'agreement_id': int(item['agreement_id']),
            'process': values.get('sei_number') or '—',
            'type': values.get('agreement_type') or 'Não informado',
            'begin': _display(values.get('signed_date')),
            'end': _display(values.get('expiry_date')),
            'status': 'Vencido',
            'origin': 'Planilha de acordos',
            'sig': values.get('sig_number') or '—',
            'progress': values.get('progress') or '—',
            'stage': values.get('ufrj_stage') or '—',
            'validity_years': values.get('validity_years') or '—',
            'coordinator': values.get('ufrj_coordinator') or '—',
            'notes': values.get('notes') or '—',
            'archived_on': _display(item['archived_at']),
            'detail_url': f'/acordos/item/{item["agreement_id"]}#agreement-university-history',
        })
    return output
