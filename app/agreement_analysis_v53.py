"""DARI v53 - análise independente de acordos, inclusive referências históricas do Access.

Não modifica acordos e não promove referências Access a instrumentos comprovados.
"""
from __future__ import annotations
from .editing_v53_30 import sorted_named,sort_key
from collections import Counter
from datetime import date, datetime
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
import json
import re
import unicodedata
from fastapi import Request, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload
from .database import get_db
from .models import Agreement, University, Country
from .reporting import predecessors
from .agreement_progress import normalize_progress, progress_order, is_pending_progress

FIELDS = ('a_type','a_status','a_university','a_country','date_from','date_to','date_mode')


def _selected(value):
    if not value: return set()
    if isinstance(value, list): return {str(v).strip() for v in value if str(v).strip()}
    return {v.strip() for v in str(value).split(',') if v.strip() and v.strip().lower() != 'all'}


def _date(value):
    try: return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError: return None


def _label_date(value):
    d=_date(value)
    return d.strftime('%d/%m/%Y') if d else '—'


def _norm(value):
    return (str(value or '').strip()).casefold()


def _status(a, today: date):
    status = (a.status or '').strip()
    if a.expiry_date and a.expiry_date <= today:
        return 'Vencido'
    if _norm(status) == 'vencido':
        return 'Vencido'
    if _norm(status) == 'ativo' and a.signed_date and a.signed_date <= today and a.expiry_date and a.expiry_date >= today:
        return 'Ativo'
    if not status:
        return 'Não informado'
    return 'A conferir'  # Nunca presumir vigência sem assinatura/validade verificáveis


def _historic_status(begin: date | None, end: date | None, today: date) -> str:
    """Status vigente HOJE de cada processo antigo, a partir das datas Access.

    A assinatura é a data de início aprovada administrativamente. Sem fim,
    a vigência não é comprovada; datas invertidas nunca geram status positivo.
    Os campos originais do Access permanecem intactos no SQLite.
    """
    if begin and end and end < begin:
        return 'Não informado'
    if end and end < today:
        return 'Vencido'
    if begin and end and begin <= today <= end:
        return 'Ativo'
    return 'Não informado'


def _matches_dates(start, end, qstart, qend, mode):
    # Sem recorte de datas, não restringir acordos atuais.
    if not qstart and not qend: return True
    if mode == 'assinatura':
        return bool(start and (not qstart or start >= qstart) and (not qend or start <= qend))
    # Vigência: início e fim se sobrepõem ao intervalo solicitado.
    if not start: return False
    return (not qend or start <= qend) and (not qstart or not end or end >= qstart)


def _institution_name(value):
    text = unicodedata.normalize('NFKD', str(value or '').strip())
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).casefold().split())


def _process_key_normalized(value):
    return ''.join(c for c in str(value or '').upper() if c.isalnum())


def _historic_institution_matches(db: Session, university_id: int):
    """Mesmo ID ou mesmo nome de universidade (com país igual).

    Associação calculada na consulta: futuros acordos passam a enxergar o
    histórico da sua universidade sem promover/duplicar registros do Access.
    """
    institution = db.get(University, university_id)
    if institution is None:
        return [], []
    all_institutions = db.scalars(select(University)).all()
    same = [u.id for u in all_institutions
            if u.id == university_id or
            (u.country_id == institution.country_id and _institution_name(u.name) == _institution_name(institution.name))]
    return institution, sorted(set(same))


def _matching_operational_agreements(db: Session, university_ids):
    if not university_ids:
        return []
    return db.scalars(select(Agreement).where(Agreement.university_id.in_(university_ids), Agreement.id.not_in(select(text('agreement_id')).select_from(text('dari_deleted_agreements_v53_46')))).order_by(Agreement.id)).all()


def _historic_type_and_link(db: Session, university_id, process, default_link=None):
    """Tipo disponível apenas se o processo puder ser identificado no cadastro atual."""
    _, matches = _historic_institution_matches(db, university_id)
    agreements = _matching_operational_agreements(db, matches)
    proc = _process_key_normalized(process)
    exact = next((a for a in agreements if proc and proc == _process_key_normalized(a.sei_number)), None)
    selected = exact or (agreements[0] if agreements else None)
    link = f'/acordos/item/{selected.id}#agreement-university-history' if selected else (default_link or f'/acordos/analise/instituicao/{university_id}')
    return (exact.agreement_type if exact and exact.agreement_type else 'Não informado'), link


def _historic_rows_for_university(db: Session, university_id: int):
    _, same = _historic_institution_matches(db, university_id)
    if not same:
        return []
    sql = """SELECT universidade_id AS uid, referencia_processo_access AS process,
             data_inicio_access AS begin_at, data_fim_access AS end_at
             FROM historico_acordos_universidades
             WHERE sinalizador_acordo_access=1
             AND universidade_id IN (""" + ','.join(str(int(uid)) for uid in same) + ") ORDER BY data_inicio_access, referencia_processo_access"
    rows = db.execute(text(sql)).mappings().all()
    out = []
    for row in rows:
        kind, _ = _historic_type_and_link(db, row['uid'], row['process'])
        out.append({'process':row['process'] or '—', 'type':kind,
                    'begin':_label_date(row['begin_at']), 'end':_label_date(row['end_at']),
                    'status':_historic_status(_date(row['begin_at']), _date(row['end_at']), date.today())})
    return out


def _historic_rows(db: Session, filters, countries, allowed_ids):
    """Referências do Access por assinatura (início histórico) ou vigência.

    Regra administrativa aprovada: a data de início do Access é considerada
    data de assinatura na análise e nas exportações. O valor bruto original
    permanece em data_inicio_access para rastreabilidade.
    """
    chosen_type={_norm(s) for s in _selected(filters['a_type'])}
    chosen_status={_norm(s) for s in _selected(filters['a_status'])}
    rows=db.execute(text("""SELECT h.universidade_id, u.name AS university, c.name AS country,
                    u.country_id AS country_id, h.referencia_processo_access AS process,
                    h.data_inicio_access AS begin_at, h.data_fim_access AS end_at
                    FROM historico_acordos_universidades h
                    JOIN universities u ON u.id=h.universidade_id
                    LEFT JOIN countries c ON c.id=u.country_id
                    WHERE h.sinalizador_acordo_access=1""")).mappings().all()
    qstart=_date(filters['date_from']);qend=_date(filters['date_to'])
    out=[]
    for r in rows:
        if allowed_ids and r['universidade_id'] not in allowed_ids:continue
        if countries and str(r['country_id']) not in countries:continue
        begin=_date(r['begin_at']);end=_date(r['end_at'])
        # Num recorte de vigência é necessário haver intervalo completo.
        # Sem ano informado, incluir também referências sem datas comprovadas.
        if (qstart or qend) and filters['date_mode'] == 'vigencia' and not (begin and end and begin <= end):continue
        if not _matches_dates(begin,end,qstart,qend,filters['date_mode']):continue
        historic_type, target = _historic_type_and_link(db, r['universidade_id'], r['process'])
        if chosen_type and _norm(historic_type) not in chosen_type:continue
        effective = _historic_status(begin, end, date.today())
        if chosen_status and _norm(effective) not in chosen_status:continue
        out.append({'id':None,'university_id':r['universidade_id'],
             'university':r['university'],'country':r['country'] or '—',
             'type':historic_type,'sig':'—','sei':r['process'] or '—',
             'progress':'Não informado','status':effective,
             'signed':begin,'expiry':end,'origin':'base_antiga',
             'detail_url':target})
    return sorted(out,key=lambda r:(r['university'].casefold(),r['sei']))




def query_agreements(db: Session, raw: dict):
    filters={k:str(raw.get(k,'') or '').strip() for k in FIELDS}
    filters['a_year'] = str(raw.get('a_year',date.today().year) if raw.get('a_year',None) is not None else date.today().year).strip()
    if filters['a_year'].lower() == 'all': filters['a_year']=''
    if filters['date_mode'] not in {'vigencia','assinatura'}:filters['date_mode']='assinatura'
    # Atalho independente: todos os acordos operacionais nas etapas 1 a 4,
    # sem restricao temporal, institucional, tipo ou vigencia.
    filters['a_expiring_180'] = '1' if str(raw.get('a_expiring_180', '')).strip() == '1' else ''
    filters['a_in_progress'] = '1' if not filters['a_expiring_180'] and str(raw.get('a_in_progress', '')).strip() == '1' else ''
    if filters['a_expiring_180']:
        # Consulta rápida baseada na data de hoje: independe de ano, assinatura,
        # país, status e demais filtros. Abrange processos atuais e históricos.
        for key in ('a_type','a_status','a_university','a_country','date_from','date_to','a_year'):
            filters[key] = ''
        filters['date_mode'] = 'vigencia'
    elif filters['a_in_progress']:
        for key in ('a_type', 'a_status', 'a_university', 'a_country', 'date_from', 'date_to', 'a_year'):
            filters[key] = ''
        filters['date_mode'] = 'vigencia'
    elif filters['a_status'] != '__none__':
        # Opcoes antigas como 'Nao informado' nao restringem novos filtros.
        statuses = ('Ativo', 'Vencido')
        filters['a_status'] = ','.join(v for v in statuses if v in _selected(filters['a_status']))
    if filters['a_year']:
        try:
            year=int(filters['a_year'])
            if not 1900 <= year <= 2100:raise ValueError()
            filters['date_from']=f'{year}-01-01'
            filters['date_to']=f'{year}-12-31'
        except ValueError:
            filters['a_year']=''
    start=_date(filters['date_from']);end=_date(filters['date_to'])
    if start and end and start>end:
        start,end=end,start
        filters['date_from'],filters['date_to']=start.isoformat(),end.isoformat()
    options={
        'types':sorted({s for s in db.scalars(select(Agreement.agreement_type).distinct()).all() if s} | {'Não informado', 'Acordo de Duplo Diploma'},key=sort_key),
        'progress':sorted({normalize_progress(s) for s in db.scalars(select(Agreement.progress).distinct()).all() if s} | {'Não informado'}, key=progress_order),
        'status':['Ativo','Vencido'],
        'universities': sorted_named(db.scalars(select(University).order_by(University.name)).all()),
        'countries': sorted_named(db.scalars(select(Country).order_by(Country.name)).all()),
    }
    chosen_type={_norm(s) for s in _selected(filters['a_type'])}
    chosen_status={_norm(s) for s in _selected(filters['a_status'])}
    chosen_countries=_selected(filters['a_country'])
    ids={int(s) for s in _selected(filters['a_university']) if s.isdigit()}
    allowed_ids=None
    if ids:
        allowed_ids=set()
        for uid in ids: allowed_ids.update(predecessors(db,uid))
    today=date.today()
    from datetime import timedelta
    expiry_limit=today+timedelta(days=180)
    current=[]
    secondary_types={}
    try:
        for agreement_id, modality in db.execute(text('SELECT agreement_id, modality FROM dari_agreement_modalities')).all():
            secondary_types.setdefault(agreement_id, set()).add(modality)
    except Exception:
        pass
    rows=db.scalars(select(Agreement).options(joinedload(Agreement.university).joinedload(University.country)).where(Agreement.id.not_in(select(text('agreement_id')).select_from(text('dari_deleted_agreements_v53_46'))))).unique().all()
    for a in rows:
        if filters['a_expiring_180'] and (a.expiry_date is None or not today <= a.expiry_date <= expiry_limit):continue
        if filters['a_in_progress'] and not is_pending_progress(a.progress):continue
        effective=_status(a,today)
        if chosen_type and not ({_norm(a.agreement_type or 'Não informado')} | {_norm(v) for v in secondary_types.get(a.id,set())}).intersection(chosen_type):continue
        if chosen_status and _norm(effective) not in chosen_status:continue
        if allowed_ids and a.university_id not in allowed_ids:continue
        if chosen_countries and str(a.university.country_id if a.university else '') not in chosen_countries:continue
        if not _matches_dates(a.signed_date,a.expiry_date,start,end,filters['date_mode']):continue
        current.append({'id':a.id,'university_id':a.university_id,
            'university':a.university.name if a.university else '—',
            'country':a.university.country.name if a.university and a.university.country else '—',
            'type':' / '.join([a.agreement_type or 'Não informado'] + sorted(secondary_types.get(a.id,set()))),'sig':a.sig_number or '—',
            'sei':a.sei_number or '—','progress':normalize_progress(a.progress) or 'Não informado',
            'status':effective,'signed':a.signed_date,'expiry':a.expiry_date,
            'origin':'cadastro','detail_url':f'/acordos/item/{a.id}'})
    current.sort(key=lambda r:(r['university'].casefold(),r['type']))
    historic=[] if filters['a_in_progress'] else _historic_rows(db,filters,chosen_countries,allowed_ids)
    if filters['a_expiring_180']:
        historic=[r for r in historic if r['expiry'] is not None and today <= r['expiry'] <= expiry_limit]
    # Se o processo identificado no Access já é representado por um acordo do mesmo
    # estabelecimento e as janelas de vigência se sobrepõem, não o somar duas vezes.
    def norm_process(v):
        return ''.join(ch for ch in str(v or '').upper() if ch.isalnum())
    current_keys={(r['university_id'],norm_process(r['sei'])) for r in current if r['sei'] not in {'','—',None}}
    historic=[r for r in historic if (r['university_id'],norm_process(r['sei'])) not in current_keys or r['sei']=='—']
    if any('__none__' in _selected(filters[k]) for k in ('a_type','a_status','a_university','a_country')):
        current=[];historic=[]
    if filters['a_expiring_180']:
        combined=sorted(current+historic,key=lambda r:(r['expiry'],r['university'].casefold(),r['sei']))
    else:
        combined=sorted(current+historic,key=lambda r:(r['university'].casefold(),r['type'],r['sei']))
    # A consulta rápida apresenta as etapas, enquanto qualquer consulta normal
    # agrupa por PAÍS. Ambos usam exatamente os mesmos registros do recorte.
    if filters['a_expiring_180']:
        summary=[]
        chart_title='Vencimentos Próximos (180 dias)'
        grouping='Vencimento'
    elif filters['a_in_progress']:
        counts=Counter(r['progress'] for r in combined)
        summary=sorted(counts.items(),key=lambda kv:(progress_order(kv[0]),kv[0].casefold()))
        chart_title='Acordos por andamento'
        grouping='Andamento'
    else:
        counts=Counter(r['country'] or 'Não informado' for r in combined)
        summary=sorted(counts.items(),key=lambda kv:(-kv[1],kv[0].casefold()))
        chart_title='Acordos'
        grouping='País'
    # Ativos são apenas os acordos com situação e datas verificadas na planilha.
    active=sum(r['status']=='Ativo' for r in current)
    in_progress=sum(is_pending_progress(r['progress']) for r in current)
    return {
        'filters':filters,'options':options,'current':current,
        'historical':historic,'agreements':combined,'summary':summary,
        'total':len(combined),'active':active,'in_progress':in_progress,
        'historical_matched':len(historic),'current_year':today.year,
        'expiry_start':today,'expiry_end':expiry_limit,
        'show_chart':not bool(filters['a_expiring_180']),
        'chart_title':chart_title,'grouping':grouping,
        'chart':{'type':'bar','labels':[k for k,_ in summary],
                 'datasets':[{'label':'Acordos','data':[v for _,v in summary]}],
                 'orientation':'horizontal'},
    }




def _fmt(v):return _label_date(v)


def _xlsx(data):
    """Excel da análise: uma lista filtrável, completa e sem gráfico.

    Utiliza os MESMOS registros já calculados para a página Visualizar.
    Os históricos do Access são incluídos com identificação de origem.
    """
    from .reporting import report_to_xlsx

    filters = data['filters']
    def selection(key):
        values = _selected(filters.get(key, ''))
        return ', '.join(sorted(values)) if values else 'Todos'

    # Os filtros efetivos são incorporados ao arquivo para rastreabilidade.
    university_names = {str(u.id): u.name for u in data['options']['universities']}
    country_names = {str(c.id): c.name for c in data['options']['countries']}
    chosen_universities = _selected(filters.get('a_university', ''))
    chosen_countries = _selected(filters.get('a_country', ''))
    university_label = ', '.join(sorted(university_names.get(v, v) for v in chosen_universities)) if chosen_universities else 'Todas'
    country_label = ', '.join(sorted(country_names.get(v, v) for v in chosen_countries)) if chosen_countries else 'Todos'

    columns = ['Universidade', 'País', 'Tipo de acordo', 'SIG+', 'SEI',
               'Andamento', 'Status de vigência', 'Assinatura', 'Fim/Validade', 'Origem']
    rows = []
    for item in data['agreements']:
        rows.append([
            item['university'], item['country'], item['type'], item['sig'],
            item['sei'], item['progress'], item['status'],
            _fmt(item['signed']), _fmt(item['expiry']),
            'Histórico (Access)' if item['origin'] == 'base_antiga' else 'Cadastro atual',
        ])

    report = {
        'title': 'DARI — Acordos do recorte',
        'description': f"{len(rows)} acordos no recorte; inclui cadastros atuais e referências históricas encontradas.",
        'filters': ([f"Recorte: Vencimentos próximos (180 dias), de {data['expiry_start']:%d/%m/%Y} a {data['expiry_end']:%d/%m/%Y}"] if filters.get('a_expiring_180') else ["Recorte: Acordos em andamento (etapas 1 a 4 anteriores a Assinado)"] if filters.get('a_in_progress') else [
            'Tipo: ' + selection('a_type'),
            'Status: ' + selection('a_status'),
            'Universidade: ' + university_label,
            'País: ' + country_label,
            'Ano: ' + (filters.get('a_year') or 'Todos'),
            'Critério temporal: ' + ('Vigência' if filters.get('date_mode') == 'vigencia' else 'Assinatura'),
        ]),
        'columns': columns,
        'rows': rows,
        # Não incluir `chart`: a exportação Excel é somente tabular.
    }
    return report_to_xlsx(report)


def _pdf(data):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle,Image
    pagesize=landscape(A4)
    f=BytesIO();doc=SimpleDocTemplate(f,pagesize=pagesize,rightMargin=35,leftMargin=35,topMargin=30,bottomMargin=32)
    styles=getSampleStyleSheet()
    cell=ParagraphStyle('DARI_cell',parent=styles['Normal'],fontSize=7.2,leading=9,textColor=colors.HexColor('#172333'))
    head=ParagraphStyle('DARI_head',parent=cell,fontName='Helvetica-Bold')
    title=ParagraphStyle('DARI_title',parent=styles['Heading1'],textColor=colors.HexColor('#172333'))
    story=[Paragraph('DARI • Escola Politécnica • UFRJ',styles['Normal']),Spacer(1,6),Paragraph(escape(data['chart_title']),title),Spacer(1,6)]
    story.append(Paragraph(f"Acordos no recorte: {data['total']} | Ativos confirmados: {data['active']} | Em andamento: {data['in_progress']}",cell))
    story.append(Spacer(1,12))
    def table(headers,rows,widths):
        values=[[Paragraph(escape(str(x)),head) for x in headers]]
        for row in rows:values.append([Paragraph(escape(str(x if x is not None else '')),cell) for x in row])
        t=LongTable(values,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EDF2F7')),('GRID',(0,0),(-1,-1),.25,colors.HexColor('#DFE6EC')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        return t
    if not data['show_chart']:
        story.append(Paragraph(f"Vencimentos entre {data['expiry_start']:%d/%m/%Y} e {data['expiry_end']:%d/%m/%Y} (inclusive).",cell))
        story.append(Spacer(1,12))
    if data['show_chart'] and data['summary']:
        from .reporting import _chart_png
        pdf_chart=data['chart']
        if data['grouping']=='País' and len(pdf_chart['labels'])>12:
            # O PDF tem altura fixa: exibir as 12 maiores barras no gráfico,
            # sem cortar países da tabela completa logo abaixo.
            pdf_chart={**pdf_chart,
                       'labels':pdf_chart['labels'][:12],
                       'datasets':[{**ds,'data':ds['data'][:12]} for ds in pdf_chart['datasets']]}
            story.append(Paragraph('Gráfico: 12 países com mais acordos; a tabela abaixo inclui todos os países.',cell))
            story.append(Spacer(1,5))
        chart_bytes=_chart_png({**pdf_chart,'title':data['chart_title']})
        story.append(Image(BytesIO(chart_bytes),width=21*28.35,height=8.5*28.35));story.append(Spacer(1,8))
    if data['show_chart']:
        story.append(Paragraph('Quantidades por '+('país' if data['grouping']=='País' else 'andamento'),styles['Heading2']))
        story.append(table([data['grouping'],'Acordos'],data['summary'],[440,105]));story.append(Spacer(1,15))
    story.append(Paragraph('Acordos do recorte',styles['Heading2']))
    rows=[(r['university'],r['type'],r['progress'],r['status'],_fmt(r['signed']),_fmt(r['expiry'])) for r in data['agreements']]
    if rows:story.append(table(['Universidade','Tipo','Andamento','Status','Assinatura','Fim'],rows,[212,135,132,75,80,80]))
    doc.build(story)
    return f.getvalue()



def _all_agreements_xlsx(db, q, columns):
    """A listagem principal de Acordos exporta somente instrumentos operacionais.

    Ações nunca entra no Excel. O filtro por colunas segue o seletor original.
    """
    from sqlalchemy.orm import selectinload
    from .reporting import report_to_xlsx
    records=db.scalars(select(Agreement).options(
        joinedload(Agreement.university).joinedload(University.country),
        selectinload(Agreement.actions)).where(Agreement.id.not_in(select(text('agreement_id')).select_from(text('dari_deleted_agreements_v53_46'))))).unique().all()
    # A pesquisa considera nome/país, identificador e tipo de acordo.
    search=(q or '').strip().casefold()
    def matches(a):
        values=(a.university.name if a.university else '',
                a.university.country.name if a.university and a.university.country else '',
                a.agreement_type, a.sig_number or '',a.sei_number or '',a.progress or '')
        return not search or any(search in str(v).casefold() for v in values)
    from .agreement_archive_v53_19 import refresh_archive
    archived_sheet_ids = refresh_archive(db)
    records=[a for a in sorted(records,key=lambda a:a.id,reverse=True) if matches(a) and a.id not in archived_sheet_ids]
    headers=['Universidade','Sigla','País','Tipo do acordo','Nº SIG+','Nº SEI','Andamento',
             'Etapa atual UFRJ','Assinatura','Vigência (anos)','Validade','Status',
             'Coordenador UFRJ','Próximo passo','Prazo','Observações']
    data=[]
    for a in records:
        last=a.actions[-1] if a.actions else None
        data.append([a.university.name,a.university.acronym or '-',
            a.university.country.name if a.university and a.university.country else '-',
            a.agreement_type,a.sig_number or '-',a.sei_number or '-',normalize_progress(a.progress) or '-',a.ufrj_stage or '-',
            _label_date(a.signed_date),a.validity_years if a.validity_years is not None else '-',
            _label_date(a.expiry_date),a.status or '-',a.ufrj_coordinator or '-',
            last.next_step if last and last.next_step else '-',
            last.deadline_text if last and last.deadline_text else '-',a.notes or '-'])
    if columns:
        values=columns.split(',')
        if not values or any(not v.isdecimal() for v in values):
            raise ValueError('Seleção de colunas inválida')
        indices=sorted(set(int(v) for v in values))
        if len(indices)!=len(values) or any(i>=len(headers) for i in indices):
            raise ValueError('Seleção de colunas inválida')
        headers=[headers[i] for i in indices]
        data=[[r[i] for i in indices] for r in data]
    report={'title':'DARI — Acordos','description':'Acordos cadastrados no sistema a partir da planilha atual',
            'filters':[f'Pesquisa: {q}' if q else 'Todos os registros'],
            'columns':headers,'rows':data}
    return report_to_xlsx(report)


def _agreement_table_xlsx(report:dict,columns:str,filename:str):
    """Exporta as colunas visíveis da tabela; a coluna Ações não participa."""
    from fastapi import HTTPException
    from .reporting import report_to_xlsx
    if columns:
        values=columns.split(',')
        if any(not value.isdecimal() for value in values):
            raise HTTPException(422,'Seleção de colunas inválida.')
        selected=[int(value) for value in values]
        if (not selected or len(selected)!=len(set(selected)) or
                any(index>=len(report['columns']) for index in selected)):
            raise HTTPException(422,'Seleção de colunas inválida.')
        chosen=sorted(selected)
        report={**report,'columns':[report['columns'][i] for i in chosen],
                'rows':[[row[i] for i in chosen] for row in report['rows']]}
    content=report_to_xlsx(report)
    return StreamingResponse(iter([content]),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':f'attachment; filename="{filename}"'})


def install_agreement_analytics_routes(app, require_view, render, common_context):
    @app.get('/acordos/analise',response_class=HTMLResponse)
    def agreement_analysis_view(request:Request,db:Session=Depends(get_db)):
        require_view(request,'acordos')
        data=query_agreements(db,dict(request.query_params))
        return render(request,'agreement_analysis_v53.html',common_context(request,'acordos') | data | {'query':dict(request.query_params)})

    @app.get('/acordos/analise/dados')
    def agreement_analysis_data(request:Request,db:Session=Depends(get_db)):
        require_view(request,'acordos')
        result=query_agreements(db,dict(request.query_params))
        return {'chart':result['chart'],'chart_title':result['chart_title'],'grouping':result['grouping'],'total':result['total'],'active':result['active'],
                'in_progress':result['in_progress'],'show_chart':result['show_chart'],
                'expiry_start':_label_date(result['expiry_start']),'expiry_end':_label_date(result['expiry_end']),
                'expiring_agreements':([{'university':r['university'],'country':r['country'],'type':r['type'],
                    'progress':r['progress'],'status':r['status'],'expiry':_label_date(r['expiry']),
                    'sei':r['sei'],'detail_url':r['detail_url'],'origin':r['origin']}
                    for r in result['agreements']] if result['filters']['a_expiring_180'] else []),
                'options':{'types':result['options']['types'],'progress':result['options']['progress'],
                           'status':result['options']['status'],
                           'universities':[{'id':u.id,'name':u.name} for u in result['options']['universities']],
                           'countries':[{'id':c.id,'name':c.name} for c in result['options']['countries']]}}

    @app.get('/acordos/analise/pdf')
    def agreement_analysis_pdf(request:Request,db:Session=Depends(get_db)):
        require_view(request,'acordos')
        data=query_agreements(db,dict(request.query_params));b=_pdf(data)
        return StreamingResponse(iter([b]),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="DARI_acordos.pdf"'})

    @app.get('/acordos/analise/xlsx')
    def agreement_analysis_excel(request:Request,db:Session=Depends(get_db)):
        require_view(request,'acordos')
        data=query_agreements(db,dict(request.query_params));b=_xlsx(data)
        return StreamingResponse(iter([b]),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="DARI_acordos.xlsx"'})

    @app.get('/acordos/analise/resumo.xlsx')
    def agreement_analysis_summary_excel(request:Request,columns:str='',db:Session=Depends(get_db)):
        require_view(request,'acordos')
        data=query_agreements(db,dict(request.query_params))
        report={
            'title':'DARI — Dados do relatório de acordos',
            'description':'Quantitativos do recorte selecionado na análise de acordos.',
            'filters':['Critérios aplicados na página de análise de acordos'],
            'columns':[data['grouping'],'Acordos'],
            'rows':[[label,amount] for label,amount in data['summary']],
        }
        return _agreement_table_xlsx(report,columns,'DARI_acordos_resumo.xlsx')

    @app.get('/acordos/analise/recorte.xlsx')
    def agreement_analysis_records_excel(request:Request,columns:str='',db:Session=Depends(get_db)):
        require_view(request,'acordos')
        data=query_agreements(db,dict(request.query_params))
        report={
            'title':'DARI — Acordos do recorte',
            'description':'Registros exibidos na tabela da análise de acordos.',
            'filters':['Critérios aplicados na página de análise de acordos'],
            'columns':['Universidade','País','Tipo','SIG+','SEI','Andamento','Status','Assinatura','Fim / validade'],
            'rows':[[item['university'],item['country'],item['type'],item['sig'],item['sei'],
                     item['progress'],item['status'],_fmt(item['signed']),_fmt(item['expiry'])]
                    for item in data['agreements']],
        }
        return _agreement_table_xlsx(report,columns,'DARI_acordos_recorte.xlsx')

    @app.get('/acordos/cadastro/xlsx')
    def agreement_full_list_excel(request:Request,q:str='',columns:str='',db:Session=Depends(get_db)):
        require_view(request,'acordos')
        try:
            content=_all_agreements_xlsx(db,q,columns)
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422,detail=str(exc))
        return StreamingResponse(iter([content]),
             media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
             headers={'Content-Disposition':'attachment; filename="DARI_acordos_cadastro.xlsx"'})

    @app.get('/acordos/analise/instituicao/{university_id}',response_class=HTMLResponse)
    def agreement_history_detail(university_id:int,request:Request,db:Session=Depends(get_db)):
        require_view(request,'acordos')
        university=db.get(University,university_id)
        if not university:
            return HTMLResponse('Instituição não encontrada.',status_code=404)
        history = _historic_rows_for_university(db, university_id)
        return render(request,'agreement_institutional_history_v53.html',common_context(request,'acordos') | {
            'university':university,'history':history})

    @app.get('/acordos/item/{agreement_id}/historico')
    def agreement_item_history(agreement_id:int,request:Request,db:Session=Depends(get_db)):
        require_view(request,'acordos')
        a=db.get(Agreement,agreement_id)
        if not a:return HTMLResponse('Acordo não encontrado.',status_code=404)
        return {'history':_historic_rows_for_university(db, a.university_id)}




def history_agreements_for_list(db: Session, search: str, current_agreements):
    """Mostra referências de acordos do Access no cadastro, sem reclassificar a vigência.

    Referências idênticas ao mesmo processo e instituição atual são deduplicadas.
    """
    present={(a.university_id, _process_key(a.sei_number)) for a in current_agreements
             if a.sei_number and _process_key(a.sei_number)}
    matches=db.execute(text("""SELECT h.universidade_id AS university_id, u.name AS university,
          c.name AS country,h.referencia_processo_access AS process,
          h.data_inicio_access AS start_date,h.data_fim_access AS end_date
          FROM historico_acordos_universidades h JOIN universities u ON u.id=h.universidade_id
          LEFT JOIN countries c ON c.id=u.country_id WHERE h.sinalizador_acordo_access=1
          ORDER BY u.name""")).mappings().all()
    search=(search or '').strip().casefold()
    result=[]
    for r in matches:
        if (r['university_id'],_process_key(r['process'])) in present and _process_key(r['process']):continue
        if search and search not in ' '.join((r['university'] or '',r['country'] or '',r['process'] or '')).casefold():continue
        result.append({'university_id':r['university_id'],'university':r['university'],
                       'country':r['country'] or '—','process':r['process'] or '—',
                       'begin_label':_label_date(r['start_date']), 'end_label':_label_date(r['end_date'])})
    return result


def _process_key(v):
    return ''.join(ch for ch in str(v or '').upper() if ch.isalnum())

# DARI_V53_19_AUTO_ARCHIVE
