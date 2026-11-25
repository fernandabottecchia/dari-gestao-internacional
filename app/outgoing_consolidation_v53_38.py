"""Dados operacionais conciliados de Outgoing, sem mutação em consultas."""
from sqlalchemy import inspect, text,select
from .models import Country

def profiles(db):
    if not inspect(db.connection()).has_table('outgoing_profile_v53_38'):return {}
    return {r['mobility_id']:dict(r) for r in db.execute(text('SELECT * FROM outgoing_profile_v53_38')).mappings()}

def attach(db,rows):
    data=profiles(db);countries={c.id:c for c in db.scalars(select(Country))}
    edited_ids=set(db.execute(text("SELECT DISTINCT record_id FROM dari_local_edits_v53_30 WHERE kind='mobility'")).scalars())
    from .mobility_schedule_v53_40 import schedules,calendar,forecast
    schedule40=schedules(db);terms40=calendar(db)
    for m in rows:
        if m.direction!='outgoing':continue
        p=data.get(m.id,{})
        m._outgoing_profile38=p
        f=forecast(m.start_semester.label if m.start_semester else None,m.duration_semesters,terms40,schedule40.get(m.id),m.outgoing_detail.expected_return if m.outgoing_detail else None,p.get('expected_return_period'),m.id in edited_ids)
        m._outgoing_return38=f['return_period']
        m._return_label40=f['expected_label']
        live=m.destination_university.country if m.destination_university else None
        m._outgoing_country38=live if m.id in edited_ids else countries.get(p.get('country_id')) or live
    return data
