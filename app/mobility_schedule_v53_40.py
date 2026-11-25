"""Período operacional e previsão recalculados a partir dos campos atuais."""
from datetime import date, datetime
import re
from sqlalchemy import inspect, text, select
from .models import Semester

TABLE_SQL = '''CREATE TABLE IF NOT EXISTS dari_mobility_schedule_v53_40 (
 mobility_id INTEGER PRIMARY KEY REFERENCES mobilities(id),
 mode TEXT NOT NULL CHECK(mode IN ('semesters','dates')),
 start_date TEXT, end_date TEXT,
 CHECK(mode='semesters' OR (start_date IS NOT NULL AND end_date IS NOT NULL AND start_date<=end_date)))'''

def table_exists(db, name):
    return inspect(db.connection()).has_table(name)

def schedules(db):
    if not table_exists(db,'dari_mobility_schedule_v53_40'):return {}
    return {r['mobility_id']:dict(r) for r in db.execute(text('SELECT * FROM dari_mobility_schedule_v53_40')).mappings()}

def calendar(db):
    if not table_exists(db,'dari_academic_calendar_cache'):return []
    return [dict(r) for r in db.execute(text('SELECT semester,start_date,end_date FROM dari_academic_calendar_cache')).mappings()]

def as_date(value):
    if isinstance(value,datetime):return value.date()
    if isinstance(value,date):return value
    if not value:return None
    for fmt in ('%Y-%m-%d','%d/%m/%Y'):
        try:return datetime.strptime(str(value),fmt).date()
        except ValueError:pass
    raise ValueError('Informe uma data válida no formato dia/mês/ano.')

def date_period(value, terms):
    day=as_date(value)
    if not day:return None
    matched={r['semester'] for r in terms if as_date(r['start_date'])<=day<=as_date(r['end_date'])}
    return next(iter(matched)) if len(matched)==1 else None

def period_for_input(value, terms, label):
    result=date_period(value,terms)
    if not result:raise ValueError(f'{label}: a data deve corresponder a um único semestre em Configurações > Calendário acadêmico. Confira as datas do calendário antes de salvar.')
    return result

def last_period(first, duration):
    if not first or not re.fullmatch(r'\d{4}/[12]',first):return None
    try:count=int(duration)
    except (TypeError,ValueError):return None
    if count<1:return None
    year,term=map(int,first.split('/'));index=year*2+term-1+count-1
    if index//2>9999:return None
    return f'{index//2:04d}/{index%2+1}'

def return_after(first,duration):
    if not last_period(first,duration):return None
    return last_period(first,int(duration)+1)

def prepare(db,mode,first,duration,start,end,return_period="",priority="duration"):
    if mode not in ('semesters','dates'):raise ValueError('Escolha semestre e duração ou datas de início e fim.')
    if mode=='dates':
        begin,finish=as_date(start),as_date(end)
        if not begin or not finish:raise ValueError('Informe a data de início e a data de fim.')
        if begin>finish:raise ValueError('A data de fim deve ser igual ou posterior à data de início.')
        terms=calendar(db)
        first=period_for_input(begin,terms,'Data de início');last=period_for_input(finish,terms,'Data de fim')
        if last<first:raise ValueError('Confira a ordem dos semestres no calendário acadêmico.')
        return dict(mode=mode,first=first,last=last,duration=None,start_date=begin.isoformat(),end_date=finish.isoformat())
    from .editing_v53_30 import valid_semester
    first=valid_semester(first)
    if priority not in ('duration','return'):raise ValueError('Controle de período inválido.')
    if priority=='return':
        last=valid_semester(return_period)
        y,t=map(int,first.split('/'));ey,et=map(int,last.split('/'))
        duration=(ey-y)*2+et-t
    if priority=='duration' and (duration is None or str(duration).strip()==''):
        return dict(mode=mode,first=first,last=None,return_period=None,duration=None,start_date=None,end_date=None)
    last=last_period(first,duration)
    if not last or not return_after(first,duration) or int(duration)>100:raise ValueError('Informe uma duração inteira entre 1 e 100 semestres.')
    return dict(mode=mode,first=first,last=last,return_period=return_after(first,duration),duration=int(duration),start_date=None,end_date=None)

def ensure_period(db,label):
    found=db.scalar(select(Semester).where(Semester.label==label))
    if found:return found
    year,term=map(int,label.split('/'));found=Semester(label=label,year=year,term=term)
    db.add(found);db.flush();return found

def save(db,m,plan):
    # Same transaction as the student's form. No commit here.
    if plan['mode']=='semesters' and plan['duration'] is not None:
        for offset in range(1,plan['duration']+1):ensure_period(db,last_period(plan['first'],offset))
        ensure_period(db,plan['return_period'])
    m.start_semester=ensure_period(db,plan['first'])
    m.end_semester=ensure_period(db,plan['last']) if plan['last'] else None
    m.duration_semesters=plan['duration']
    db.execute(text('''INSERT INTO dari_mobility_schedule_v53_40(mobility_id,mode,start_date,end_date)
      VALUES (:mid,:mode,:start_date,:end_date) ON CONFLICT(mobility_id) DO UPDATE SET
      mode=excluded.mode,start_date=excluded.start_date,end_date=excluded.end_date'''),dict(mid=m.id,**{k:plan[k] for k in ('mode','start_date','end_date')}))
    if m.outgoing_detail:m.outgoing_detail.expected_return=as_date(plan['end_date'])
    if m.direction=='outgoing' and table_exists(db,'outgoing_profile_v53_38'):
        db.execute(text('''INSERT INTO outgoing_profile_v53_38(mobility_id,expected_return_period,source_sha)
          VALUES (:mid,:period,'manual') ON CONFLICT(mobility_id) DO UPDATE SET expected_return_period=excluded.expected_return_period'''),{'mid':m.id,'period':plan.get('return_period',plan['last'])})

def forecast(first,duration,terms,schedule=None,expected=None,imported_period=None,edited=False):
    schedule=schedule or {}
    if schedule.get('mode')=='dates':
        expected=as_date(schedule['end_date']);period=date_period(expected,terms)
        basis='Data de fim e calendário acadêmico' if period else 'Data de fim; conferir calendário acadêmico'
    elif schedule.get('mode')=='semesters' or edited:
        expected=None;period=return_after(first,duration);basis='Semestre de início e duração'
    elif expected:
        expected=as_date(expected);period=date_period(expected,terms);basis='Data prevista e calendário acadêmico' if period else 'Data prevista; conferir calendário acadêmico'
    elif return_after(first,duration):
        expected=None;period=return_after(first,duration);basis='Semestre de início e duração'
    elif imported_period:
        period=imported_period;expected=None;basis='Semestre de retorno informado'
    else:
        expected=None;period=return_after(first,duration);basis='Semestre de início e duração'
    return dict(expected=expected,return_period=period,expected_label=expected.strftime('%d/%m/%Y') if expected else (period or ''),basis=basis)

def context(db,m):
    if m is None:return {'schedule40':{'mode':'semesters','start_date':'','end_date':''}}
    schedule=schedules(db).get(m.id,{})
    expected=m.outgoing_detail.expected_return if m.direction=='outgoing' and m.outgoing_detail else None
    if m.direction=='incoming' and table_exists(db,'incoming_operational_supplements'):
        row=db.execute(text('SELECT expected_departure FROM incoming_operational_supplements WHERE mobility_id=:mid'),{'mid':m.id}).first()
        if row:
            try:expected=as_date(row[0])
            except ValueError:expected=None
    edited=bool(db.execute(text("SELECT 1 FROM dari_local_edits_v53_30 WHERE kind='mobility' AND record_id=:mid LIMIT 1"),{'mid':m.id}).first()) if table_exists(db,'dari_local_edits_v53_30') else False
    f=forecast(m.start_semester.label if m.start_semester else None,m.duration_semesters,calendar(db),schedule,expected,getattr(m,'_outgoing_return38',None),edited)
    return {'schedule40':dict(mode='semesters',start_date='',end_date='')|schedule,'forecast40':f,'calendar_terms41':calendar(db)}

def sync_dates(db):
    # Called inside the calendar-cache transaction so filters follow calendar edits.
    from .models import Mobility
    terms=calendar(db)
    for mid,s in schedules(db).items():
        if s['mode']!='dates':continue
        m=db.get(Mobility,mid)
        if not m:continue
        first=date_period(s['start_date'],terms);last=date_period(s['end_date'],terms)
        m.start_semester=ensure_period(db,first) if first else None
        m.end_semester=ensure_period(db,last) if last else None
        if m.direction=='outgoing' and table_exists(db,'outgoing_profile_v53_38'):
            db.execute(text('UPDATE outgoing_profile_v53_38 SET expected_return_period=:p WHERE mobility_id=:mid'),{'p':last,'mid':mid})
