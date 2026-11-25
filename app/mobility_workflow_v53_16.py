"""DARI v53.16: situacao de acompanhamento, independente do status historico.

Cada mobilidade possui classificacao explicita (ativo / concluido) ou nenhuma.
A classificacao automatica inicial exige duas datas confirmadas; previsoes e
situacoes textuais de sistemas antigos nao sao prova de intercâmbio em curso.
"""
from __future__ import annotations
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

TABLE = 'dari_mobility_workflow'
LOG = 'dari_mobility_workflow_log'


def ensure_schema(engine):
    if not inspect(engine).has_table(TABLE):
        raise RuntimeError('Banco ainda não migrado. Execute tools.migrate_sqlite.')


def state_map(db: Session, ids=None):
    if ids is not None and not ids:
        return {}
    if ids is None:
        rows=db.execute(text('SELECT mobility_id,situation FROM dari_mobility_workflow')).all()
    else:
        # O SQLite possui limite de parametros em algumas instalacoes.
        ids=[int(v) for v in ids]
        rows=[]
        for i in range(0,len(ids),500):
            chunk=ids[i:i+500]
            holders=','.join(':'+str(n) for n in range(len(chunk)))
            rows += db.execute(text('SELECT mobility_id,situation FROM dari_mobility_workflow WHERE mobility_id IN ('+holders+')'),
                  {str(n):mid for n,mid in enumerate(chunk)}).all()
    result={int(r[0]):r[1] for r in rows}
    if inspect(db.connection()).has_table('incoming_conclusions_v53_33'):
        manual=set(db.execute(text("SELECT DISTINCT mobility_id FROM dari_mobility_workflow_log WHERE reason='alteracao manual na ficha'")).scalars())
        allowed=set(ids) if ids is not None else None
        for mid in db.execute(text('SELECT mobility_id FROM incoming_conclusions_v53_33')).scalars():
            if mid not in manual and (allowed is None or mid in allowed):result.setdefault(mid,'completed')
    if inspect(db.connection()).has_table('outgoing_conclusions_v53_38'):
        manual=set(db.execute(text("SELECT DISTINCT mobility_id FROM dari_mobility_workflow_log WHERE reason='alteracao manual na ficha'")).scalars())
        allowed=set(ids) if ids is not None else None
        for mid in db.execute(text('SELECT mobility_id FROM outgoing_conclusions_v53_38')).scalars():
            if mid not in manual and (allowed is None or mid in allowed):result.setdefault(mid,'completed')
    return result


def state_of(db: Session, mobility_id: int):
    return state_map(db,[mobility_id]).get(int(mobility_id))


def active_ids(db: Session):
    from .mobility_periods_v53_29 import activity
    return activity(db)


def change_state(db: Session, mobility_id: int, action: str, username: str, *, commit=True, force=False):
    if action not in ('active','completed','unclassify'):
        raise ValueError('Acao invalida')
    # O chamador valida a permissao antes de gravar.
    previous=state_of(db,mobility_id)
    # A conclusao manual independe de uma classificacao anterior como ativo.
    next_state = None if action=='unclassify' else action
    if next_state == previous and not force:
        return previous
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    if next_state is None:
        db.execute(text('DELETE FROM dari_mobility_workflow WHERE mobility_id=:mid'),{'mid':mobility_id})
    else:
        db.execute(text('''INSERT INTO dari_mobility_workflow (mobility_id,situation,source,changed_at,changed_by)
               VALUES (:mid,:state,'manual',:dt,:user)
               ON CONFLICT(mobility_id) DO UPDATE SET situation=excluded.situation,
               source=excluded.source,changed_at=excluded.changed_at,changed_by=excluded.changed_by'''),
               {'mid':mobility_id,'state':next_state,'dt':now,'user':username})
    db.execute(text('''INSERT INTO dari_mobility_workflow_log
               (mobility_id,previous_situation,new_situation,changed_at,changed_by,reason)
               VALUES (:mid,:prev,:next,:dt,:user,'alteracao manual na ficha')'''),
               {'mid':mobility_id,'prev':previous,'next':next_state,'dt':now,'user':username})
    if commit:db.commit()
    return next_state

# DARI_V53_18_FICHA_ALUNO_CONCLUSAO
