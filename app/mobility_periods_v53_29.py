"""Semestres de presença e atividade atual, independentes da conclusão administrativa."""
import re, unicodedata
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import inspect, text, select
from .models import Mobility, Semester

def key(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',str(value or '')) if not unicodedata.combining(c)).casefold().split())

def modality(value):
    return {'duplo diploma':'Duplo Diploma','intercambio simples':'Intercâmbio Simples','inercambio simples':'Intercâmbio Simples','apenas estagio':'Estágio','estagio de pesquisa':'Estágio'}.get(key(value),value)

def semester(value):
    match=re.fullmatch(r'(\d{4})[./-]([1-3])',str(value or '').strip())
    return '/'.join(match.groups()) if match else None

def selected_periods(value):
    return sorted({semester(v) for v in str(value or '').split(',') if semester(v)})

def period_filter(value):
    if value=='nao_informado':return value
    return ','.join(selected_periods(value)) or 'todos'

def tables(db):
    return set(inspect(db.connection()).get_table_names())

def records(db,sql):
    return db.execute(text(sql)).mappings().all()

def presence(db, mobilities=None):
    mobilities=mobilities if mobilities is not None else db.scalars(select(Mobility)).all()
    result={m.id:set() for m in mobilities}
    available=tables(db)
    # Associação por mobility_id homologado, nunca por posição ou nome do aluno.
    for table,col in [('incoming_recent_semester_evidence','semester_label'),('incoming_v51_semester_membership','semester_label'),('incoming_semester_activity_evidence','reference_semester'),('dari_mobility_periods_v53_29','semester_label')]:
        if table in available:
            for row in records(db,f'SELECT mobility_id,{col} AS period FROM {table} WHERE mobility_id IS NOT NULL'):
                p=semester(row['period'])
                if row['mobility_id'] in result and p:result[row['mobility_id']].add(p)
    if 'incoming_source_rows' in available:
        for row in records(db,"SELECT mobility_id,sheet_name FROM incoming_source_rows WHERE review_status IN ('linked_exact','linked_high_confidence','linked_human_approved','linked_consolidated_v53_33','promoted_recent_provisional') AND mobility_id IS NOT NULL"):
            p=semester(row['sheet_name'])
            if row['mobility_id'] in result and p:result[row['mobility_id']].add(p)
    if 'incoming_profile_v53_33' in available:
        consolidated={r['mobility_id']:set() for r in records(db,'SELECT mobility_id FROM incoming_profile_v53_33')}
        for r in records(db,"SELECT mobility_id,semester_label FROM dari_mobility_periods_v53_29 WHERE source IN ('planilha_consolidada_v53_33','historico_consolidado_v53_33')"):
            if r['mobility_id'] in consolidated:consolidated[r['mobility_id']].add(r['semester_label'])
        for mid,ps in consolidated.items():
            if mid in result and ps:result[mid]=ps
    known={semester(s.label) for s in db.scalars(select(Semester))} - {None}
    edited_ids=set(db.execute(text("SELECT DISTINCT record_id FROM dari_local_edits_v53_30 WHERE kind='mobility'")).scalars())
    from .mobility_schedule_v53_40 import schedules,calendar,as_date
    schedule40=schedules(db);terms40=calendar(db)
    for m in mobilities:
        cfg=schedule40.get(m.id,{})
        if cfg.get('mode')=='dates':
            begin,end=as_date(cfg['start_date']),as_date(cfg['end_date'])
            result[m.id]={r['semester'] for r in terms40 if as_date(r['start_date'])<=end and as_date(r['end_date'])>=begin}
            continue
        first=semester(m.start_semester.label) if m.start_semester else None
        last=semester(m.end_semester.label) if m.end_semester else None
        # As ocorrências importadas são a evidência prioritária, inclusive períodos especiais /3.
        if m.id in edited_ids and first:
            result[m.id]={p for p in known if first<=p<=(last or first)} | {first,last or first}
        if result[m.id]:continue
        if first:result[m.id].add(first)
        if first and last and first<=last:
            result[m.id].update(p for p in known if first<=p<=last)
            result[m.id].add(last)
    return result

def _date(value):
    try:return date.fromisoformat(str(value)[:10])
    except (TypeError,ValueError):return None

def activity(db,mobilities=None,today=None,periods=None):
    today=today or datetime.now(timezone(timedelta(hours=-3))).date()
    mobilities=mobilities if mobilities is not None else db.scalars(select(Mobility)).all()
    periods=periods if periods is not None else presence(db,mobilities)
    available=tables(db)
    workflow={r['mobility_id']:r for r in records(db,'SELECT * FROM dari_mobility_workflow')}
    from .mobility_workflow_v53_16 import state_map
    completed={mid for mid,state in state_map(db).items() if state=='completed'}
    overrides={}
    for r in records(db,"SELECT * FROM dari_mobility_workflow_log WHERE reason='alteracao manual na ficha' ORDER BY id"):
        overrides[r['mobility_id']]=r['new_situation']
    supplements={r['mobility_id']:r for r in records(db,'SELECT * FROM incoming_operational_supplements')} if 'incoming_operational_supplements' in available else {}
    consolidated={r['mobility_id'] for r in records(db,'SELECT mobility_id FROM incoming_profile_v53_33')} if 'incoming_profile_v53_33' in available else set()
    current=set()
    if 'dari_academic_calendar_cache' in available:
        for r in records(db,'SELECT * FROM dari_academic_calendar_cache'):
            begin,end=_date(r['start_date']),_date(r['end_date'])
            if begin and end and begin<=today<=end:current.add(semester(r['semester']))
    from .mobility_schedule_v53_40 import schedules,as_date
    schedule40=schedules(db)
    result=set()
    for m in mobilities:
        mid=m.id;w=workflow.get(mid,{})
        if m.direction not in ('incoming','outgoing'):continue
        if mid in completed or (mid in overrides and overrides[mid] is None):continue
        if overrides.get(mid)=='active':
            result.add(mid);continue
        if any(s in key(m.status) for s in ('nao homologado','cancelad','desistente','rejeitad')):continue
        cfg=schedule40.get(mid,{})
        if cfg.get('mode')=='dates':
            if as_date(cfg['start_date'])<=today<=as_date(cfg['end_date']):result.add(mid)
            continue
        s={} if mid in consolidated else supplements.get(mid,{})
        arrival,departure=_date(s.get('confirmed_arrival')),_date(s.get('confirmed_departure'))
        if (arrival and arrival>today) or (departure and departure<today) or (arrival and departure and arrival>departure):continue
        if arrival and departure and arrival<=today<=departure:
            result.add(mid);continue
        if periods.get(mid,set()) & current:
            result.add(mid);continue
        # Confirmação manual sem intervalo conhecido continua válida; com intervalo,
        # não estende uma mobilidade passada para sempre.
        if w.get('source')=='manual' and w.get('situation')=='active':
            last=semester(m.end_semester.label) if m.end_semester else None
            first=semester(m.start_semester.label) if m.start_semester else None
            now=f'{today.year}/{1 if today.month<=6 else 2}'
            if (not first or first<=now) and (not last or now<=last):result.add(mid)
    return result

def listing(db,rows,periodo='todos',processo='todos',situacao='todos'):
    from .mobility_presentation_v53_34 import attach
    attach(db,rows)
    from .outgoing_consolidation_v53_38 import attach as outgoing_attach
    outgoing_attach(db,rows)
    periodo=period_filter(periodo)
    selected=set(selected_periods(periodo))
    processo=processo if processo in ('abertos','concluidos') else 'todos'
    situacao=situacao if situacao in ('ativos','periodo') else 'todos'
    from .mobility_workflow_v53_16 import state_map
    states=state_map(db,[m.id for m in rows]);periods=presence(db,rows);active=activity(db,rows,periods=periods)
    scoped=[m for m in rows if periodo=='todos' or (periodo=='nao_informado' and not periods.get(m.id)) or bool(selected & periods.get(m.id,set()))]
    counts={'todos':len(scoped),'ativos':sum(m.id in active for m in scoped),'concluidos':sum(states.get(m.id)=='completed' for m in scoped),'periodo':len(scoped) if periodo!='todos' else 0}
    rows=[m for m in scoped if (processo!='concluidos' or states.get(m.id)=='completed') and (processo!='abertos' or states.get(m.id)!='completed') and (situacao!='ativos' or m.id in active) and (situacao!='periodo' or periodo!='todos')]
    labels={m.id:([('active','Ativo')] if m.id in active else [])+([('period','Ativo no período')] if periodo!='todos' and bool(selected & periods.get(m.id,set())) else [])+([('completed','Processo concluído')] if states.get(m.id)=='completed' else []) for m in rows}
    for m in rows:
        if m.direction in ('incoming','outgoing') and any(v in key(m.status) for v in ('desist','cancel','rejeit')):
            labels[m.id]=[(k,'Registro no período' if k=='period' else v) for k,v in labels[m.id]]+[('neutral','Desistente / cancelado')]
    from .period_filters_v53_41 import used_periods
    options=sorted(used_periods(db),reverse=True)
    return rows,dict(selected_periods=selected,periodo=periodo,processo=processo,situacao=situacao,status_labels=labels,workflow_state_map=states,status_counts=counts,semesters_incoming=options,period_labels={mid:' · '.join(sorted(ps)) for mid,ps in periods.items()})

def ensure_semester(db,value,fallback):
    if not str(value or '').strip():return fallback
    from .editing_v53_30 import valid_semester
    label=valid_semester(value)
    found=db.scalar(select(Semester).where(Semester.label==label))
    if found:return found.id
    year,term=map(int,label.split('/'))
    found=Semester(label=label,year=year,term=term);db.add(found);db.flush()
    return found.id
