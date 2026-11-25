"""Consultas administrativas com tabelas padrão e fichas editáveis."""
import io,json,re,hmac,hashlib
from datetime import date
from urllib.parse import urlencode,urlsplit,parse_qsl
from fastapi import Request,Depends,HTTPException
from fastapi.responses import HTMLResponse,Response,RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from . import admin_queries_v53_25 as previous
from . import admin_queries_v53_24 as old
rows=previous.rows
SLUGS=previous.SLUGS|{'professores':'Professores','cursos':'Cursos','coordenadores-cursos':'Coordenadores de cursos'}
KINDS={'projetos','professores','cursos','coordenadores-cursos','universidades','parceiras'}
STUDENTS={'previsao-retorno-estudantes','alunos-ativos'}
COLUMNS={
 'professores':[('name','Nome'),('course','Curso (principal)'),('email','E-mail'),('phone','Telefone'),('contact','Contato'),('room','Sala')],
 'cursos':[('name','Nome'),('number','Número do curso')],
 'coordenadores-cursos':[('course','Curso'),('coordinator','Coordenador'),('room','Sala'),('phone','Telefone'),('email','E-mail')],
}
FIELDS={
 'projetos':[('code','Projeto (sigla e número)','text'),('name','Nome do projeto (completo)','text'),('brazilian_coordinator_id','Coordenador brasileiro UFRJ','professor'),('modality','Modalidade do projeto','text'),('start_date','Data de início','date'),('end_date','Data de fim','date'),('foreign_coordinator','Coordenador estrangeiro','text'),('phone','Telefone de contato','text'),('email','E-mail','text'),('notes','Observações do projeto','textarea')],
 'professores':[('name','Nome','text'),('course_id','Curso (principal)','course'),('email','E-mail','text'),('phone','Telefone','text'),('contact','Contato','text'),('room','Sala','text'),('notes','Observações','textarea')],
 'cursos':[('name','Nome','text'),('number','Número do curso','text')],
 'coordenadores-cursos':[('course_id','Curso','course'),('coordinator_id','Coordenador','professor'),('room','Sala','text'),('phone','Telefone','text'),('email','E-mail','text')],
 'universidades':[('contacts','Contatos e nomes das pessoas','textarea'),('emails','E-mails (um por linha)','textarea')],
 'parceiras':[('name','Nome da parceira','text'),('contacts','Contatos e nomes das pessoas','textarea'),('emails','E-mails (um por linha)','textarea'),('notes','Observações','textarea')]
}
META_TABLE={'projetos':('dari_project_details_v53_26','project_id'),'professores':('dari_professor_details_v53_26','professor_id'),'cursos':('dari_course_details_v53_26','course_id'),'coordenadores-cursos':('dari_course_coordinators_v53_26','id'),'universidades':previous.TABLES['universidades'],'parceiras':previous.TABLES['parceiras']}

def safe_back(value,default='/#consultas-administrativas'):
    if not isinstance(value,str) or len(value)>4000 or any(c in value for c in ('\\','\r','\n')):return default
    p=urlsplit(value)
    if p.scheme or p.netloc or p.path not in ['/consultas-administrativas/'+s for s in SLUGS]:return default
    return p.path+('?' + urlencode([(k,v) for k,v in parse_qsl(p.query) if k in ('fluxo','periodo','busca','pagina','ativos')]) if p.query else '')
legacy_destination=previous.legacy_destination

def raw_records(db,kind):
    if kind=='professores':return rows(db,'''SELECT p.id,p.name,p.email,x.course_id,c.name AS course,COALESCE(x.phone,'') phone,COALESCE(x.contact,'') contact,COALESCE(x.room,'') room,COALESCE(x.notes,'') notes,COALESCE(x.version,0) version FROM coordinators p LEFT JOIN dari_professor_details_v53_26 x ON x.professor_id=p.id LEFT JOIN courses c ON c.id=x.course_id ORDER BY p.name,p.id''')
    if kind=='cursos':return rows(db,'''SELECT c.id,c.name,COALESCE(x.number,'') number,COALESCE(x.version,0) version FROM courses c LEFT JOIN dari_course_details_v53_26 x ON x.course_id=c.id ORDER BY c.name,c.id''')
    if kind=='coordenadores-cursos':return rows(db,'''SELECT x.*,c.name AS course,p.name AS coordinator FROM dari_course_coordinators_v53_26 x LEFT JOIN courses c ON c.id=x.course_id LEFT JOIN coordinators p ON p.id=x.coordinator_id ORDER BY c.name,x.id''')
    if kind=='projetos':return rows(db,'''SELECT p.id,p.name,p.code,x.brazilian_coordinator_id,c.name AS brazilian_coordinator,COALESCE(x.modality,'') modality,COALESCE(x.start_date,'') start_date,COALESCE(x.end_date,'') end_date,COALESCE(x.foreign_coordinator,'') foreign_coordinator,COALESCE(x.phone,'') phone,COALESCE(x.email,'') email,COALESCE(x.notes,'') notes,COALESCE(x.version,0) version,COALESCE(f.active,0) active FROM programs p LEFT JOIN dari_project_details_v53_26 x ON x.project_id=p.id LEFT JOIN coordinators c ON c.id=x.brazilian_coordinator_id LEFT JOIN dari_project_flags_v53_25 f ON f.project_id=p.id ORDER BY p.name,p.id''')
    if kind=='universidades':return previous.university_rows(db)
    if kind=='parceiras':return rows(db,'SELECT * FROM dari_partner_deadlines_v53_25 ORDER BY name,id')
    raise HTTPException(404,'Cadastro não encontrado.')

CREATE_LABELS={'projetos':'Novo projeto','professores':'Novo professor','coordenadores-cursos':'Novo coordenador de curso','universidades':'Novo e-mail de universidade'}
DELETABLE={'projetos','professores','coordenadores-cursos','universidades'}
def records(db,kind):
    deleted={r['record_id'] for r in rows(db,'SELECT record_id FROM dari_admin_deleted_v53_27 WHERE kind=:kind',{'kind':kind})}
    return [r for r in raw_records(db,kind) if r['id'] not in deleted]

def deadline_summary(record,today=None):
    today=today or old.local_today();future=[];past=[];labels=[]
    for item in json.loads(record['deadlines_json']):
        d=old.parse_date(item.get('value'))
        if d:(future if d>=today else past).append(d)
        labels.append(item.get('label','')+': '+(old.date_label(d) if d else 'Sem data definida'))
    upcoming=min(future) if future else None
    record.update(active=int(bool(upcoming)),active_label='Sim' if upcoming else 'Não',next_deadline=old.date_label(upcoming) if upcoming else 'Prazos encerrados' if past else 'Sem data definida',deadline_details='\n'.join(labels),_sort=(0,upcoming) if upcoming else (1 if past else 2,date.max))
    return record

def link_items(record):
    return [{'label':x.get('title') or 'Link útil','url':x.get('url','')} for x in json.loads(record.get('links_json','[]'))]

def detail(db,kind,ident):
    found=next((r for r in records(db,kind) if r['id']==ident),None)
    if found is None:raise HTTPException(404,'Registro não encontrado.')
    if kind=='projetos':
        found['universities']=rows(db,'''SELECT u.id,u.name,u.acronym FROM universities u JOIN dari_project_universities_v53_26 x ON x.university_id=u.id WHERE x.project_id=:id ORDER BY u.name''',{'id':ident})
        found['university_ids']=[r['id'] for r in found['universities']]
    if kind=='parceiras':deadline_summary(found);found.pop('_sort',None)
    return found

def result_for(db,slug,flow='',period='',search='',active=False,today=None):
    if slug not in SLUGS:raise HTTPException(404,'Consulta não encontrada.')
    if slug=='deadline-parceiras':
        data=[deadline_summary(r,today) for r in records(db,'parceiras')]
        if active:data=[r for r in data if r['active']]
        data.sort(key=lambda r:(r['_sort'],old.normalized(r['name'])))
        columns=[('name','Parceira'),('next_deadline','Próximo deadline'),('deadline_details','Prazos'),('contacts','Contato / pessoa'),('emails','E-mail'),('links','Links úteis'),('active_label','Prazo em aberto')]
    elif slug in COLUMNS:data=records(db,slug);columns=COLUMNS[slug]
    else:
        result=previous.result_for(db,slug,flow,period,search,active,today)
        if slug=='projetos':
            available={r['id'] for r in records(db,'projetos')};result['rows']=[r for r in result['rows'] if r['id'] in available]
        return result
    if search.strip():data=[r for r in data if old.normalized(search.strip()) in old.normalized(' '.join(str(r.get(k) or '') for k,_ in columns))]
    return dict(title=SLUGS[slug],description='',rows=data,columns=columns,periods=[])

def excel_bytes(result,columns=''):
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill,Alignment
    from openpyxl.utils import get_column_letter
    fields=result['columns']
    if columns:
        if not re.fullmatch(r'\d+(?:,\d+)*',columns):raise ValueError('Seleção de colunas inválida.')
        indexes=list(dict.fromkeys(int(i) for i in columns.split(',')))
        if any(i>=len(fields) for i in indexes):raise ValueError('Seleção de colunas inválida.')
        fields=[fields[i] for i in indexes]
    wb=Workbook();ws=wb.active;ws.title='Consulta'
    ws.append([result['title']]);ws['A1'].font=Font(size=16,bold=True)
    ws.append(['Filtros',result.get('filter_label','')]);ws.append([]);ws.append([])
    ws.append([label for _,label in fields])
    for cell in ws[5]:cell.font=Font(bold=True,color='17324D');cell.fill=PatternFill('solid',fgColor='EDF3F6')
    for record in result['rows']:
        values=[record.get(k) if record.get(k) is not None else '' for k,_ in fields]
        ws.append([re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',v) if isinstance(v,str) else v for v in values])
        for cell in ws[ws.max_row]:
            if isinstance(cell.value,str):
                cell.value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',cell.value);cell.data_type='s'
    for i,(k,label) in enumerate(fields,1):
        width=max([len(label)]+[max((len(line) for line in str(r.get(k) or '').splitlines()),default=0) for r in result['rows']]);ws.column_dimensions[get_column_letter(i)].width=min(65,max(14,width+2))
    for row in ws:
        for cell in row:
            cell.alignment=Alignment(vertical='top',wrap_text=True)
            if isinstance(cell.value,str):cell.data_type='s'
    ws.freeze_panes='A6';ws.auto_filter.ref=f'A5:{get_column_letter(len(fields))}{max(5,ws.max_row)}'
    out=io.BytesIO();wb.save(out);return out.getvalue()

def install_routes(app,require_view,can_edit,render,common_context,get_db,secret,cookie_name):
    def token(request):return hmac.new(secret.encode(),('admin27:'+request.cookies.get(cookie_name,'')).encode(),hashlib.sha256).hexdigest()
    def context(request,db,kind,record,back,editing=False,new=False,saved=False,error=''):
        fields=FIELDS[kind];groups=[]
        def group(title,keys):
            items=[]
            for key,label,typ in fields:
                if key not in keys:continue
                value=record.get(key,'')
                if key=='brazilian_coordinator_id':value=record.get('brazilian_coordinator','')
                elif key=='course_id':value=record.get('course','')
                elif key=='coordinator_id':value=record.get('coordinator','')
                elif typ=='date':value=old.date_label(old.parse_date(value))
                items.append((label,value or ''))
            groups.append({'title':title,'items':items})
        if kind=='projetos':
            group('Projeto',['code','name','modality']);group('Coordenação e contato',['brazilian_coordinator_id','foreign_coordinator','phone','email']);group('Período',['start_date','end_date']);group('Observações',['notes'])
        elif kind=='parceiras':group('Parceira',['name']);group('Contatos',['contacts','emails']);group('Observações',['notes'])
        else:group({'universidades':'Contatos','professores':'Professor','cursos':'Curso','coordenadores-cursos':'Coordenação do curso'}[kind],[k for k,_,_ in fields])
        if kind=='universidades':groups.insert(0,{'title':'Universidade','items':[('Nome',record.get('name','')),('Sigla',record.get('acronym') or ''),('País',record.get('country') or '')]})
        available_professors={p['id'] for p in records(db,'professores')}
        return common_context(request,'dashboard')|dict(kind=kind,record=record,fields=fields,groups=groups,back=safe_back(back),csrf=token(request),editable=can_edit(request,'acordos'),editing=editing,new=new,saved=saved,error=error,professors=[r for r in raw_records(db,'professores') if r['id'] in available_professors or r['id'] in (record.get('coordinator_id'),record.get('brazilian_coordinator_id'))],courses=rows(db,'SELECT id,name FROM courses ORDER BY name'),universities=rows(db,'SELECT id,name,acronym FROM universities ORDER BY name') if editing and (kind=='projetos' or kind=='universidades' and new) else [],deadlines=[dict(d,display=old.date_label(old.parse_date(d.get('value')))) for d in json.loads(record.get('deadlines_json','[]'))],links=link_items(record),create_label=CREATE_LABELS.get(kind,''),can_delete=kind in DELETABLE,edit_url=f'/cadastros-administrativos/{kind}/{record.get("id",0)}?'+urlencode({'voltar':safe_back(back),'editar':'1'}),view_url=f'/cadastros-administrativos/{kind}/{record.get("id",0)}?'+urlencode({'voltar':safe_back(back)}))
    @app.get('/consultas-administrativas/{slug}',response_class=HTMLResponse)
    def query(request:Request,slug:str,fluxo:str='',periodo:str='',busca:str='',pagina:int=1,formato:str='',ativos:str='',columns:str='',db=Depends(get_db)):
        require_view(request,'dashboard')
        if slug=='alunos-em-aberto':return RedirectResponse('/consultas-administrativas/alunos-ativos'+('?' + request.url.query if request.url.query else ''),303)
        if formato not in ('','xlsx','csv'):raise HTTPException(400,'Formato inválido.')
        try:
            result=result_for(db,slug,fluxo,periodo,busca,ativos=='1')
            result['filter_label']=' | '.join(v for v in [fluxo or 'Todos',periodo,busca,'Somente ativos' if ativos=='1' else ''] if v)
            if formato=='xlsx':return Response(excel_bytes(result,columns),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{slug}.xlsx"'})
            if formato=='csv':return Response(old.csv_content(result),media_type='text/csv; charset=utf-8')
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        total=len(result['rows']);pages=max(1,(total+49)//50);page=min(max(1,pagina),pages)
        params={k:v for k,v in dict(fluxo=fluxo,periodo=periodo,busca=busca,ativos=ativos).items() if v};qs=urlencode(params)
        back='/consultas-administrativas/'+slug+'?'+urlencode(params|{'pagina':page})
        for r in result['rows']:
            kind='parceiras' if slug=='deadline-parceiras' else 'universidades' if slug=='lista-emails-universidades' else slug
            path=('/mobilidade/' if slug in STUDENTS else '/cadastros-administrativos/'+kind+'/')+str(r['id'])
            r['open_url']=path+'?'+urlencode({'voltar':back});r['edit_url']=r['open_url']+'&editar=1'
            r['link_items']=link_items(r) if slug=='deadline-parceiras' else previous.link_items(r.get('links',''))
        result.update(rows=result['rows'][(page-1)*50:page*50],slug=slug,fluxo=fluxo,periodo=periodo,busca=busca,ativos=ativos,query=qs,total=total,page=page,pages=pages,editable=can_edit(request,'acordos'),xlsx_url='?'+urlencode(params|{'formato':'xlsx'}),create_label=CREATE_LABELS.get('universidades' if slug=='lista-emails-universidades' else slug,'') if slug!='universidades' else '',new_url='/cadastros-administrativos/'+('universidades' if slug=='lista-emails-universidades' else slug)+'/novo?'+urlencode({'voltar':back}))
        return render(request,'admin_queries_v53_27.html',common_context(request,'dashboard')|result)
    @app.get('/cadastros-administrativos/{kind}/novo',response_class=HTMLResponse)
    def new_record(request:Request,kind:str,voltar:str='',db=Depends(get_db)):
        require_view(request,'dashboard')
        if kind not in CREATE_LABELS:raise HTTPException(404)
        if not can_edit(request,'acordos'):raise HTTPException(403)
        return render(request,'admin_detail_v53_27.html',context(request,db,kind,{'name':'','version':0,'university_ids':[],'active':0},voltar,True,True))
    @app.get('/cadastros-administrativos/{kind}/{ident}',response_class=HTMLResponse)
    def open_detail(request:Request,kind:str,ident:int,voltar:str='',editar:str='',salvo:str='',db=Depends(get_db)):
        require_view(request,'dashboard');record=detail(db,kind,ident)
        if editar=='1' and not can_edit(request,'acordos'):raise HTTPException(403)
        return render(request,'admin_detail_v53_27.html',context(request,db,kind,record,voltar,editar=='1',saved=salvo=='1'))
    async def save(request,kind,ident,db,new=False):
        require_view(request,'dashboard')
        if not can_edit(request,'acordos') or not request.cookies.get(cookie_name):raise HTTPException(403)
        if kind not in KINDS:raise HTTPException(404)
        body=await request.body()
        if len(body)>250000:raise HTTPException(413,'Formulário muito grande.')
        try:pairs=parse_qsl(body.decode('utf-8'),keep_blank_values=True)
        except UnicodeError:raise HTTPException(400,'Formulário inválido.')
        form=dict(pairs)
        if not hmac.compare_digest(form.get('csrf',''),token(request)):raise HTTPException(403,'Sessão inválida. Reabra a página.')
        record={'version':0,'name':'','university_ids':[]} if new else detail(db,kind,ident);changes={};uids=record.get('university_ids',[])
        try:
            version=int(form.get('version','-1'))
            if version!=record['version']:raise HTTPException(409,'Este cadastro foi alterado em outra janela. Reabra a página antes de salvar.')
            toggle=kind=='projetos' and form.get('action')=='toggle'
            if toggle:changes={'active':0 if record['active'] else 1}
            else:
                for key,label,typ in FIELDS[kind]:
                    val=form.get(key,'').strip()
                    if len(val)>40000:raise ValueError('Texto muito longo: '+label)
                    if typ in ('course','professor'):
                        val=int(val) if val else None;table='courses' if typ=='course' else 'coordinators'
                        if val is not None and not rows(db,f'SELECT id FROM {table} WHERE id=:id',{'id':val}):raise ValueError('Selecione um cadastro válido: '+label)
                    elif typ=='date' and val:val=date.fromisoformat(val).isoformat()
                    changes[key]=val
                if 'name' in changes and not changes['name']:raise ValueError('Informe o nome.')
                for k in ('email','emails'):
                    if k in changes:
                        addresses=re.split(r'[\s;,]+',changes[k]);addresses=[a for a in addresses if a]
                        if any(not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',a) for a in addresses):raise ValueError('Informe endereços de e-mail válidos.')
                        changes[k]='\n'.join(dict.fromkeys(addresses))
                if kind=='projetos':changes['active']=int(form.get('active')=='1')
                if kind=='projetos':
                    if changes['start_date'] and changes['end_date'] and changes['start_date']>changes['end_date']:raise ValueError('A data de fim deve ser igual ou posterior à data de início.')
                    uids=list(dict.fromkeys(int(v) for k,v in pairs if k=='university_id'))
                    known={r['id'] for r in rows(db,'SELECT id FROM universities')}
                    if any(u not in known for u in uids):raise ValueError('Universidade inválida.')
                if kind=='parceiras':
                    count=int(form.get('deadline_count','0'))
                    if not 0<=count<=100:raise ValueError('Quantidade de prazos inválida.')
                    entries=[];original=json.loads(record['deadlines_json'])
                    for i in range(count):
                        if form.get(f'delete_{i}')=='1':continue
                        label=form.get(f'label_{i}','').strip();value=form.get(f'date_{i}','').strip()
                        if i>=len(original) and not label and not value:continue
                        if value:value=date.fromisoformat(value).isoformat()
                        entry=dict(original[i]) if i<len(original) else {'raw':'','source':'Cadastro manual'}
                        entry.update(label=label,value=value);entries.append(entry)
                    changes['deadlines_json']=json.dumps(entries,ensure_ascii=False)
                    link_count=int(form.get('link_count','0'))
                    if not 0<=link_count<=100:raise ValueError('Quantidade de links inválida.')
                    links=[]
                    for i in range(link_count):
                        if form.get(f'link_delete_{i}')=='1':continue
                        title=form.get(f'link_title_{i}','').strip();url=form.get(f'link_url_{i}','').strip()
                        if not title and not url:continue
                        if not url:raise ValueError('Informe o endereço de cada link.')
                        parsed=urlsplit(url)
                        if parsed.scheme not in ('http','https') or not parsed.netloc or re.search(r'\s',url) or '|' in url:raise ValueError('Informe um link http ou https válido.')
                        if len(title)>300 or '\n' in title or '|' in title:raise ValueError('Título de link inválido.')
                        if len(url)>8000 or '\n' in url:raise ValueError('Link inválido.')
                        links.append({'title':title or 'Link útil','url':url})
                    changes['links_json']=json.dumps(links,ensure_ascii=False)
                    changes['links']='\n'.join(x['title']+' | '+x['url'] for x in links)
                if kind=='coordenadores-cursos' and (not changes['course_id'] or not changes['coordinator_id']):raise ValueError('Selecione o curso e o coordenador.')
                if kind=='universidades' and new and not changes['emails']:raise ValueError('Informe ao menos um e-mail.')
            before=json.dumps(record,ensure_ascii=False)
            if new:
                if kind=='projetos':ident=db.execute(text('INSERT INTO programs(name,code) VALUES (:name,:code) RETURNING id'),changes).scalar_one()
                elif kind=='professores':ident=db.execute(text('INSERT INTO coordinators(name,email) VALUES (:name,:email) RETURNING id'),changes).scalar_one()
                elif kind=='coordenadores-cursos':ident=db.execute(text('INSERT INTO dari_course_coordinators_v53_26(course_id,coordinator_id) VALUES (:course_id,:coordinator_id) RETURNING id'),changes).scalar_one()
                elif kind=='universidades':
                    ident=int(form.get('university_id','0'))
                    current=previous.detail(db,'universidades',ident)
                    if current['emails'].strip():raise ValueError('Esta universidade já possui e-mails. Abra o cadastro existente para editá-los.')
                    version=current['version']
                    before=json.dumps(current,ensure_ascii=False)
            table,key=META_TABLE[kind]
            if kind not in ('coordenadores-cursos','parceiras'):db.execute(text(f'INSERT INTO {table} ({key}) VALUES (:id) ON CONFLICT DO NOTHING'),{'id':ident})
            base_keys={'projetos':('name','code'),'professores':('name','email'),'cursos':('name',)}.get(kind,())
            metadata={k:v for k,v in changes.items() if k not in base_keys and not (kind=='projetos' and k=='active')}
            params=metadata|{'id':ident,'version':version}
            sql=f'UPDATE {table} SET '+','.join(k+'=:'+k for k in metadata)+(',' if metadata else '')+'version=version+1 WHERE '+key+'=:id AND version=:version'
            if db.execute(text(sql),params).rowcount!=1:db.rollback();raise HTTPException(409,'Cadastro alterado em outra janela. Reabra a página.')
            base={k:changes[k] for k in base_keys if k in changes}
            if base:
                basetable={'projetos':'programs','professores':'coordinators','cursos':'courses'}[kind]
                db.execute(text(f'UPDATE {basetable} SET '+','.join(k+'=:'+k for k in base)+' WHERE id=:id'),base|{'id':ident})
            if kind=='projetos':
                db.execute(text('INSERT INTO dari_project_flags_v53_25(project_id) VALUES (:id) ON CONFLICT DO NOTHING'),{'id':ident})
                db.execute(text('UPDATE dari_project_flags_v53_25 SET active=:active,version=version+1 WHERE project_id=:id'),{'id':ident,'active':changes['active']})
                if not toggle:
                    old_ids=set(record.get('university_ids',[]))
                    for uid in old_ids-set(uids):db.execute(text('DELETE FROM dari_project_universities_v53_26 WHERE project_id=:id AND university_id=:uid'),{'id':ident,'uid':uid})
                    for uid in set(uids)-old_ids:db.execute(text('INSERT INTO dari_project_universities_v53_26(project_id,university_id) VALUES (:id,:uid)'),{'id':ident,'uid':uid})
                    changes['university_ids']=uids
            actor=getattr(request.state,'user',None);user=actor.get('username','') if isinstance(actor,dict) else getattr(actor,'username','')
            db.execute(text('INSERT INTO dari_admin_audit_v53_25(kind,record_id,username,before_json,after_json) VALUES (:kind,:id,:user,:before,:after)'),{'kind':kind,'id':ident,'user':user,'before':before,'after':json.dumps(changes,ensure_ascii=False)})
            db.commit()
        except (ValueError,IntegrityError) as exc:
            db.rollback();error='Já existe um cadastro com esse nome.' if isinstance(exc,IntegrityError) else str(exc)
            record.update({k:form.get(k,'') for k,_,_ in FIELDS[kind]});record['university_ids']=uids
            response=render(request,'admin_detail_v53_27.html',context(request,db,kind,record,form.get('voltar',''),True,new,error=error));response.status_code=400;return response
        return RedirectResponse(f'/cadastros-administrativos/{kind}/{ident}?'+urlencode({'voltar':safe_back(form.get('voltar','')),'salvo':'1'}),303)
    @app.post('/cadastros-administrativos/{kind}/novo')
    async def create_record(request:Request,kind:str,db=Depends(get_db)):
        if kind not in CREATE_LABELS:raise HTTPException(404)
        return await save(request,kind,None,db,True)
    @app.post('/cadastros-administrativos/{kind}/{ident}/excluir')
    async def delete_record(request:Request,kind:str,ident:int,db=Depends(get_db)):
        require_view(request,'dashboard')
        if not can_edit(request,'acordos') or not request.cookies.get(cookie_name):raise HTTPException(403)
        if kind not in DELETABLE:raise HTTPException(404)
        form=dict(parse_qsl((await request.body()).decode('utf-8'),keep_blank_values=True))
        if not hmac.compare_digest(form.get('csrf',''),token(request)) or form.get('confirm_delete')!='1':raise HTTPException(400,'Confirme a exclusão.')
        record=detail(db,kind,ident)
        try:version=int(form.get('version','-1'))
        except ValueError:raise HTTPException(400,'Versão inválida.')
        if version!=record['version']:raise HTTPException(409,'Cadastro alterado em outra janela. Reabra a página.')
        table,key=META_TABLE[kind]
        db.execute(text(f'INSERT INTO {table} ({key}) VALUES (:id) ON CONFLICT DO NOTHING'),{'id':ident})
        if db.execute(text(f'UPDATE {table} SET version=version+1 WHERE {key}=:id AND version=:version'),{'id':ident,'version':version}).rowcount!=1:
            db.rollback();raise HTTPException(409,'Cadastro alterado em outra janela.')
        actor=getattr(request.state,'user',None);user=actor.get('username','') if isinstance(actor,dict) else getattr(actor,'username','')
        if kind=='universidades':
            db.execute(text("UPDATE dari_university_contacts_v53_25 SET contacts='',emails='' WHERE university_id=:id"),{'id':ident})
        else:
            db.execute(text('INSERT INTO dari_admin_deleted_v53_27(kind,record_id,deleted_by) VALUES (:kind,:id,:user)'),{'kind':kind,'id':ident,'user':user})
            if kind=='projetos':db.execute(text('UPDATE dari_project_flags_v53_25 SET active=0,version=version+1 WHERE project_id=:id'),{'id':ident})
        db.execute(text('INSERT INTO dari_admin_audit_v53_25(kind,record_id,username,before_json,after_json) VALUES (:kind,:id,:user,:before,:after)'),{'kind':kind,'id':ident,'user':user,'before':json.dumps(record,ensure_ascii=False),'after':json.dumps({'action':'delete','preserved_historical_references':True})})
        db.commit()
        return RedirectResponse('/consultas-administrativas/'+('lista-emails-universidades' if kind=='universidades' else kind)+'?excluido=1',303)
    @app.post('/cadastros-administrativos/{kind}/{ident}')
    async def save_detail(request:Request,kind:str,ident:int,db=Depends(get_db)):return await save(request,kind,ident,db)
