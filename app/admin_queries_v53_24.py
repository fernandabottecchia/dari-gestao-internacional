"""Consultas administrativas atuais, somente leitura do SQLite operacional."""
from datetime import date, datetime, timezone, timedelta
import csv
import io
import re
import unicodedata
from urllib.parse import urlencode

META = {
    'previsao-retorno-estudantes': ('Previsão Retorno Estudantes', 'Estudantes com data prevista de retorno registrada.'),
    'alunos-em-aberto': ('Alunos em Aberto', 'Incoming e Outgoing em intercâmbio na data atual, conforme as confirmações registradas.'),
    'projetos': ('Projetos', 'Projetos e programas já cadastrados no sistema.'),
}
STUDENT_COLUMNS = [('direction_label','Fluxo'),('name','Estudante'),('dre','DRE'),('university','Universidade'),('start_semester','Semestre de início')]

def local_today():
    return datetime.now(timezone(timedelta(hours=-3))).date()

def parse_date(value):
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    if not value: return None
    try: return date.fromisoformat(str(value)[:10])
    except ValueError: return None

def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').casefold()) if not unicodedata.combining(c))

def date_label(value):
    return value.strftime('%d/%m/%Y') if value else ''

def semester_of(value):
    return f'{value.year}/{1 if value.month <= 6 else 2}'

def load_data(db):
    from sqlalchemy import inspect, text
    def rows(sql): return [dict(r) for r in db.execute(text(sql)).mappings().all()]
    tables=set(inspect(db.connection()).get_table_names())
    mobilities=rows('''SELECT m.id,m.direction,m.status,m.duration_semesters,s.full_name AS name,s.dre,
      u.name AS university,se.label AS start_semester,o.expected_return
      FROM mobilities m JOIN students s ON s.id=m.student_id
      LEFT JOIN universities u ON u.id=CASE WHEN m.direction='incoming' THEN m.home_university_id ELSE m.destination_university_id END
      LEFT JOIN semesters se ON se.id=m.start_semester_id
      LEFT JOIN outgoing_details o ON o.mobility_id=m.id
      WHERE m.direction IN ('incoming','outgoing')''')
    supplements={}
    if 'incoming_operational_supplements' in tables:
        for r in rows('SELECT * FROM incoming_operational_supplements ORDER BY id'):
            supplements[r['mobility_id']]=r
    workflow={r['mobility_id']:r for r in rows('SELECT * FROM dari_mobility_workflow')} if 'dari_mobility_workflow' in tables else {}
    unclassified=set()
    if 'dari_mobility_workflow_log' in tables:
        for r in rows('''SELECT l.* FROM dari_mobility_workflow_log l
          WHERE l.id=(SELECT MAX(x.id) FROM dari_mobility_workflow_log x WHERE x.mobility_id=l.mobility_id)'''):
            if r['new_situation'] is None and r['reason']=='alteracao manual na ficha':unclassified.add(r['mobility_id'])
    from .mobility_periods_v53_29 import activity
    active29=activity(db)
    from .outgoing_consolidation_v53_38 import profiles
    outgoing_profiles38=profiles(db)
    from .mobility_schedule_v53_40 import schedules,calendar,forecast
    schedules40=schedules(db);terms40=calendar(db)
    edited40={r['record_id'] for r in rows("SELECT DISTINCT record_id FROM dari_local_edits_v53_30 WHERE kind='mobility'")} if 'dari_local_edits_v53_30' in tables else set()
    for m in mobilities:
        m['active29']=m['id'] in active29
        m['expected_period38']=outgoing_profiles38.get(m['id'],{}).get('expected_return_period')
        s=supplements.get(m['id'],{}) if m['direction']=='incoming' else {}
        m.update(expected=parse_date(s.get('expected_departure') if m['direction']=='incoming' else m['expected_return']),
                 arrival=parse_date(s.get('confirmed_arrival')), departure=parse_date(s.get('confirmed_departure')),
                 workflow=workflow.get(m['id'],{}), unclassified=m['id'] in unclassified)
        m['direction_label']=m['direction'].title()
        m['forecast40']=forecast(m['start_semester'],m['duration_semesters'],terms40,schedules40.get(m['id']),m['expected'],m['expected_period38'],m['id'] in edited40)
    projects=rows('''SELECT p.id,p.name,p.code,COUNT(m.id) AS mobility_count FROM programs p
      LEFT JOIN mobilities m ON m.program_id=p.id AND m.direction IN ('incoming','outgoing')
      AND COALESCE(m.status,'') <> 'Pré-cadastro — planilha (não homologado)'
      GROUP BY p.id,p.name,p.code ORDER BY p.name,p.id''')
    historical=set()
    if 'legado_mapa' in tables:
        historical={r['id_destino'] for r in rows("SELECT id_destino FROM legado_mapa WHERE tabela_origem='tprojetos' AND tabela_destino='programs'")}
    for p in projects:p['origin']='Cadastro histórico do Access' if p['id'] in historical else 'Cadastro do sistema'
    return mobilities,projects

def eligible(m,include_provisional=False):
    status=normalized(m.get('status'))
    excluded=('desistente','cancelad','rejeitad') + (() if include_provisional else ('nao homologado',))
    return m.get('direction') in ('incoming','outgoing') and not any(x in status for x in excluded)

def activity_basis(m,today):
    if 'active29' in m:return 'Atividade atual confirmada por período, datas ou equipe' if m['active29'] else None
    w=m.get('workflow') or {}
    if w.get('situation')=='completed':return None
    arrival,departure=m.get('arrival'),m.get('departure')
    if arrival and departure and arrival>departure:return None
    if arrival and arrival>today:return None
    if departure and departure<today:return None
    if w.get('situation')=='active' and w.get('source')=='manual':return 'Atividade confirmada pela equipe'
    if m.get('unclassified'):return None
    if arrival and departure and arrival<=today<=departure:return 'Datas de chegada e saída confirmadas'
    return None

def build_result(slug,mobilities,projects,direction='',period='',q='',today=None):
    if slug not in META:raise ValueError('Consulta não encontrada.')
    if direction not in ('','incoming','outgoing'):raise ValueError('Fluxo inválido.')
    if period and not re.fullmatch(r'(?:19|20|21)\d{2}/[12]',period):raise ValueError('Período inválido. Use ano/semestre, por exemplo 2026/2.')
    today=today or local_today();rows=[];periods=set()
    if slug=='projetos':
        columns=[('code','Código'),('name','Projeto / programa'),('mobility_count','Mobilidades vinculadas'),('origin','Origem do cadastro')]
        rows=[dict(p) for p in projects]
        note='Os registros históricos não indicam, por si só, que o projeto ainda está vigente.'
    else:
        columns=STUDENT_COLUMNS+([('expected_label','Retorno previsto'),('return_period','Período do retorno'),('registration','Cadastro'),('basis','Origem da previsão')] if slug=='previsao-retorno-estudantes' else [('arrival_label','Chegada confirmada'),('departure_label','Saída confirmada'),('basis','Confirmação de atividade')])
        for m in mobilities:
            if not eligible(m,include_provisional=slug=='previsao-retorno-estudantes'):continue
            r=dict(m)
            r['registration']='Pré-cadastro — não homologado' if 'nao homologado' in normalized(m.get('status')) else 'Cadastro operacional'
            if slug=='previsao-retorno-estudantes':
                f=m.get('forecast40')
                if f is None:
                    f={'expected':m.get('expected'),'return_period':m.get('expected_period38'),'expected_label':date_label(m.get('expected')) or m.get('expected_period38'),'basis':'Previsão cadastrada'}
                if not f.get('expected') and not f.get('return_period'):continue
                p=f.get('return_period')
                if p:periods.add(p)
                if period and p!=period:continue
                r.update(f)
                r['expected_label']=date_label(f.get('expected'))
                r['return_period']=p or 'Conferir calendário acadêmico'
            else:
                basis=activity_basis(m,today)
                if not basis:continue
                r.update(arrival_label=date_label(m.get('arrival')),departure_label=date_label(m.get('departure')),basis=basis)
            if direction and m['direction']!=direction:continue
            rows.append(r)
        rows.sort(key=lambda r:(r.get('expected') or date.max,normalized(r['name']),r['id']))
        note=('Somente datas previstas registradas, incluindo pré-cadastros identificados como não homologados. Esta consulta não aprova candidaturas. Não são estimadas datas a partir da duração ou do semestre. Período do retorno: janeiro a junho (1) ou julho a dezembro (2).'
              if slug=='previsao-retorno-estudantes' else
              'Consulta recalculada para '+date_label(today)+'. Considera intervalos confirmados e atividade marcada pela equipe. Conclusões registradas e saídas confirmadas anteriores a hoje são excluídas. Previsões e status antigos, sozinhos, não comprovam atividade atual.')
    if q.strip():rows=[r for r in rows if normalized(q.strip()) in normalized(' '.join(str(r.get(k) or '') for k,_ in columns))]
    if slug=='previsao-retorno-estudantes':
        note='Retorno por semestre: início mais duração. Retorno por data: data de fim associada ao calendário acadêmico.'
        if not any(r.get('expected') for r in rows):columns=[c for c in columns if c[0]!='expected_label']
    return dict(title=META[slug][0],description=META[slug][1],columns=columns,rows=rows,note=note,periods=sorted(periods,reverse=True),today=date_label(today))

def csv_content(result):
    out=io.StringIO();out.write('\ufeff');writer=csv.writer(out,delimiter=';')
    writer.writerow([label for _,label in result['columns']])
    for r in result['rows']:
        values=[]
        for k,_ in result['columns']:
            v=r.get(k,'')
            if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')):v="'"+v
            values.append(v)
        writer.writerow(values)
    return out.getvalue()

def legacy_destination(slug,ano='',semestre='',busca='',texto='',export=False):
    mapping={'previsao-retorno-brasileiros':('previsao-retorno-estudantes','outgoing'),
             'previsao-retorno-estrangeiros':('previsao-retorno-estudantes','incoming'),
             'alunos-em-aberto':('alunos-em-aberto',''),'alunos-por-projeto':('projetos','')}
    if slug not in mapping:return None
    target,direction=mapping[slug];params={'fluxo':direction,'busca':busca or texto}
    if ano and semestre and re.fullmatch(r'(?:19|20|21)\d{2}/[12]',f'{ano}/{semestre}'):params['periodo']=f'{ano}/{semestre}'
    if export:params['formato']='csv'
    return '/consultas-administrativas/'+target+'?'+urlencode({k:v for k,v in params.items() if v})

def install_routes(app,require_view,render,common_context,get_db):
    from fastapi import Depends,HTTPException,Request
    from fastapi.responses import HTMLResponse,Response
    @app.get('/consultas-administrativas/{slug}',response_class=HTMLResponse)
    def admin_query(request:Request,slug:str,fluxo:str='',periodo:str='',busca:str='',pagina:int=1,formato:str='',db=Depends(get_db)):
        require_view(request,'dashboard')
        if slug not in META:raise HTTPException(404,'Consulta não encontrada.')
        if formato not in ('','csv'):raise HTTPException(400,'Formato inválido.')
        try:result=build_result(slug,*load_data(db),direction=fluxo,period=periodo,q=busca)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        if formato=='csv':return Response(csv_content(result),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{slug}.csv"'})
        total=len(result['rows']);pages=max(1,(total+49)//50);page=min(max(1,pagina),pages)
        result.update(rows=result['rows'][(page-1)*50:page*50],total=total,page=page,pages=pages,slug=slug,
          fluxo=fluxo,periodo=periodo,busca=busca,query=urlencode({'fluxo':fluxo,'periodo':periodo,'busca':busca}))
        return render(request,'admin_queries_v53_24.html',common_context(request,'dashboard')|result)
