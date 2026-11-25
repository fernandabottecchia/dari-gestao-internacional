"""Calendário acadêmico no banco único da aplicação hospedada."""
from __future__ import annotations
import re
from datetime import date
from sqlalchemy import text
from sqlalchemy.orm import Session

FORMAT = re.compile(r"^20\d{2}/[12]$")
TABLE_SQL = """CREATE TABLE IF NOT EXISTS dari_academic_calendar_cache (
    semester VARCHAR(6) PRIMARY KEY,
    start_date VARCHAR(10) NOT NULL,
    end_date VARCHAR(10) NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0,
    updated_at VARCHAR(40) NOT NULL
)"""


def validate_term(semester: str, start_date: str, end_date: str):
    semester = (semester or "").strip()
    if not FORMAT.fullmatch(semester):
        raise ValueError("Semestre inválido. Use AAAA/1 ou AAAA/2, por exemplo 2026/2.")
    try:
        ini, fim = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except (ValueError, TypeError):
        raise ValueError("Informe as duas datas válidas de início e término.")
    if ini > fim:
        raise ValueError("A data inicial deve ser anterior ou igual à data final.")
    if ini.year < 2000 or fim.year > 2100:
        raise ValueError("Confira o intervalo: as datas devem estar entre 2000 e 2100.")
    return semester, ini.isoformat(), fim.isoformat()


def ensure_cache(db: Session):
    # Schema is installed once by the migration command.
    pass


def read_cache(db: Session):
    ensure_cache(db)
    rows = db.execute(text("""SELECT semester, start_date, end_date, is_current, updated_at
           FROM dari_academic_calendar_cache ORDER BY semester DESC""")).mappings().all()
    return [dict(r) for r in rows]


def save_cache(db: Session, payload: dict):
    ensure_cache(db)
    sem, ini, fim = validate_term(payload['semester'], payload['start_date'], payload['end_date'])
    if payload.get('is_current'):
        db.execute(text("UPDATE dari_academic_calendar_cache SET is_current = 0"))
    db.execute(text("""INSERT INTO dari_academic_calendar_cache
     (semester, start_date, end_date, is_current, updated_at)
     VALUES (:sem, :ini, :fim, :cur, :updated)
     ON CONFLICT(semester) DO UPDATE SET
       start_date=excluded.start_date, end_date=excluded.end_date,
       is_current=excluded.is_current, updated_at=excluded.updated_at"""),
      dict(sem=sem, ini=ini, fim=fim, cur=int(bool(payload.get('is_current'))),
           updated=payload.get('updated_at','')))
    from .mobility_schedule_v53_40 import sync_dates
    sync_dates(db)
    db.commit()


def replace_cache(db: Session, records: list[dict]):
    # Sync manual e explícito; transacional no SQLite.
    ensure_cache(db)
    verified=[]
    seen=set()
    for r in records:
        sem, ini, fim = validate_term(r['semester'],r['start_date'],r['end_date'])
        if sem in seen: raise ValueError('Semestre duplicado recebido do servidor.')
        seen.add(sem)
        verified.append(dict(semester=sem,start_date=ini,end_date=fim,
            is_current=int(bool(r.get('is_current'))),updated_at=r.get('updated_at') or ''))
    if sum(int(r['is_current']) for r in verified)>1:
        raise ValueError('Servidor retornou múltiplos semestres atuais.')
    try:
        db.execute(text('DELETE FROM dari_academic_calendar_cache'))
        for r in verified:
            db.execute(text("""INSERT INTO dari_academic_calendar_cache
            (semester,start_date,end_date,is_current,updated_at)
            VALUES (:semester,:start_date,:end_date,:is_current,:updated_at)"""), r)
        from .mobility_schedule_v53_40 import sync_dates
        sync_dates(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
