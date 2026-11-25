"""Opções de período derivadas dos processos atuais, sem apagar o calendário."""
from sqlalchemy import select,text
from types import SimpleNamespace
from .models import Mobility,Semester

def used_periods(db):
    from .mobility_periods_v53_29 import presence
    from .mobility_schedule_v53_40 import schedules,calendar,forecast,table_exists
    from .outgoing_consolidation_v53_38 import profiles
    rows=db.scalars(select(Mobility).where(Mobility.direction.in_(['incoming','outgoing']))).all()
    periods=set().union(*presence(db,rows).values()) if rows else set()
    schedule=schedules(db);terms=calendar(db);out=profiles(db)
    edited=set(db.execute(text("SELECT DISTINCT record_id FROM dari_local_edits_v53_30 WHERE kind='mobility'")).scalars())
    supplements={}
    if table_exists(db,'incoming_operational_supplements'):
        supplements={r['mobility_id']:r['expected_departure'] for r in db.execute(text('SELECT mobility_id,expected_departure FROM incoming_operational_supplements')).mappings()}
    from .admin_queries_v53_24 import parse_date
    for m in rows:
        first=m.start_semester.label if m.start_semester else None
        if first:periods.add(first)
        expected=(m.outgoing_detail.expected_return if m.outgoing_detail else None) if m.direction=='outgoing' else parse_date(supplements.get(m.id))
        f=forecast(first,m.duration_semesters,terms,schedule.get(m.id),expected,out.get(m.id,{}).get('expected_return_period'),m.id in edited)
        if f['return_period']:periods.add(f['return_period'])
    return sorted(periods)

def semester_options(db):
    existing={s.label:s for s in db.scalars(select(Semester))}
    return [existing.get(label) or SimpleNamespace(id=None,label=label,year=int(label[:4]),term=int(label[-1])) for label in used_periods(db)]
