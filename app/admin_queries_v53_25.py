"""Consultas e cadastros administrativos DARI v53.25."""
import json,re,hmac,hashlib,secrets
from datetime import date
from urllib.parse import urlencode,urlsplit,parse_qsl
from fastapi import Request,HTTPException,Depends
from fastapi.responses import HTMLResponse,Response,RedirectResponse
from sqlalchemy import text
from . import admin_queries_v53_24 as old

SLUGS={'previsao-retorno-estudantes':'Previsão Retorno Estudantes','alunos-ativos':'Alunos ativos','projetos':'Projetos','universidades':'Universidades','lista-emails-universidades':'Lista E-mails Universidades','deadline-parceiras':'Deadline parceiras'}
TABLES={'projetos':('dari_project_flags_v53_25','project_id'),'universidades':('dari_university_contacts_v53_25','university_id'),'parceiras':('dari_partner_deadlines_v53_25','id')}

def safe_back(value,default='/#consultas-administrativas'):
    if not isinstance(value,str) or len(value)>4000 or any(c in value for c in ('\\','\r','\n')):return default
    p=urlsplit(value)
    if p.scheme or p.netloc or p.path not in ['/consultas-administrativas/'+s for s in SLUGS]:return default
    return p.path+('?' + urlencode([(k,v) for k,v in parse_qsl(p.query) if k in ('fluxo','periodo','busca','pagina','ativos')]) if p.query else '')

def legacy_destination(slug,*args,**kwargs):
    if slug in ('universidades','lista-emails-universidades'):
        busca=kwargs.get('busca','') or (args[2] if len(args)>2 else '') or (args[3] if len(args)>3 else '')
        return '/consultas-administrativas/'+slug+'?'+urlencode({'busca':busca,**({'formato':'csv'} if kwargs.get('export') else {})})
    target=old.legacy_destination(slug,*args,**kwargs)
    return target.replace('/alunos-em-aberto','/alunos-ativos') if target else None

def deadline_date(value,today):
    if not value:return None
    if not re.fullmatch(r'\d{2}/\d{2}(?:/\d{4})?',value):raise ValueError('Use DD/MM para prazo anual ou DD/MM/AAAA para uma data específica.')
    parts=[int(x) for x in value.split('/')];d,m=parts[:2]
    if len(parts)==3:return date(parts[2],m,d)
    date(2000,m,d)
    for y in range(today.year,today.year+9):
        try:result=date(y,m,d)
        except ValueError:continue
        if result>=today:return result

def deadline_summary(row,today):
    entries=json.loads(row['deadlines_json']);future=[];past=[]
    for item in entries:
        try:d=deadline_date(item.get('value',''),today)
        except ValueError:d=None
        if d is not None:(future if d>=today else past).append(d)
    nextdate=min(future) if future else None
    row['next_deadline']=old.date_label(nextdate) if nextdate else ('Prazos encerrados' if past else 'Sem data exata')
    row['deadline_details']='\n'.join(x.get('label','')+': '+(x.get('value') or x.get('raw') or 'Sem data')+(' (anual)' if len(x.get('value',''))==5 else '') for x in entries)
    row['_sort']=(0,nextdate,old.normalized(row['name'])) if nextdate else (1 if past else 2,date.max,old.normalized(row['name']))
    return row

def rows(db,sql,params=None):return [dict(r) for r in db.execute(text(sql),params or {}).mappings()]

def university_rows(db):
    return rows(db,'''SELECT u.id,u.name,u.acronym,c.name AS country,COALESCE(x.contacts,'') AS contacts,COALESCE(x.emails,'') AS emails,COALESCE(x.version,0) AS version
      FROM universities u LEFT JOIN countries c ON c.id=u.country_id LEFT JOIN dari_university_contacts_v53_25 x ON x.university_id=u.id ORDER BY u.name,u.id''')

def result_for(db,slug,flow='',period='',search='',active=False,today=None):
    today=today or old.local_today()
    if slug in ('previsao-retorno-estudantes','alunos-ativos','projetos'):
        result=old.build_result('alunos-em-aberto' if slug=='alunos-ativos' else slug,*old.load_data(db),direction=flow,period=period,q=search,today=today)
        if slug=='projetos':
            flags={r['project_id']:r['active'] for r in rows(db,'SELECT * FROM dari_project_flags_v53_25')}
            for r in result['rows']:r['active_label']='Sim' if flags.get(r['id']) else 'Não'
            if active:result['rows']=[r for r in result['rows'] if flags.get(r['id'])]
            result['columns'].append(('active_label','Ativo'))
    else:
        if slug=='deadline-parceiras':
            data=[deadline_summary(r,today) for r in rows(db,'SELECT * FROM dari_partner_deadlines_v53_25')]
            if active:data=[r for r in data if r['active']]
            data.sort(key=lambda r:r['_sort'])
            for r in data:r['active_label']='Sim' if r['active'] else 'Não'
            columns=[('name','Parceira'),('next_deadline','Próximo deadline'),('deadline_details','Prazos'),('contacts','Contato / pessoa'),('emails','E-mail'),('links','Links úteis'),('active_label','Ativo')]
        else:
            data=university_rows(db)
            if slug=='lista-emails-universidades':data=[r for r in data if r['emails'].strip()]
            columns=[('name','Universidade'),('acronym','Sigla'),('country','País')]+([('contacts','Contato / pessoa'),('emails','E-mail')] if slug=='lista-emails-universidades' else [])
        if search.strip():data=[r for r in data if old.normalized(search.strip()) in old.normalized(' '.join(str(r.get(k) or '') for k,_ in columns))]
        result=dict(rows=data,columns=columns,periods=[])
    result.update(title=SLUGS[slug],description='',note='')
    return result

def valid_links(value):
    for line in value.splitlines():
        if not line.strip():continue
        url=line.partition('|')[2].strip() if '|' in line else line.strip()
        p=urlsplit(url)
        if p.scheme not in ('http','https') or not p.netloc or any(c.isspace() for c in url):raise ValueError('Links úteis: informe um endereço http ou https por linha, opcionalmente precedido de título | .')
    return value

def link_items(value):
    out=[]
    for line in (value or '').splitlines():
        label,sep,url=line.partition('|');url=url.strip() if sep else label.strip()
        if urlsplit(url).scheme in ('http','https'):out.append({'label':label.strip() if sep else 'Abrir link','url':url})
    return out

def detail(db,kind,ident):
    if kind=='projetos':
        data=rows(db,'''SELECT p.id,p.name,p.code,COALESCE(f.active,0) active,COALESCE(f.version,0) version FROM programs p LEFT JOIN dari_project_flags_v53_25 f ON p.id=f.project_id WHERE p.id=:id''',{'id':ident})
    elif kind=='universidades':data=[r for r in university_rows(db) if r['id']==ident]
    elif kind=='parceiras':data=rows(db,'SELECT * FROM dari_partner_deadlines_v53_25 WHERE id=:id',{'id':ident})
    else:data=[]
    if not data:raise HTTPException(404,'Registro não encontrado.')
    return data[0]

def install_routes(app,require_view,can_edit,render,common_context,get_db,secret,cookie_name):
    def token(request):
        # The authenticated session is the CSRF secret; never emit the session cookie.
        return hmac.new(secret.encode(),('admin25:'+request.cookies.get(cookie_name,'')).encode(),hashlib.sha256).hexdigest()
    @app.get('/consultas-administrativas/{slug}',response_class=HTMLResponse)
    def query(request:Request,slug:str,fluxo:str='',periodo:str='',busca:str='',pagina:int=1,formato:str='',ativos:str='',db=Depends(get_db)):
        require_view(request,'dashboard')
        if slug=='alunos-em-aberto':return RedirectResponse('/consultas-administrativas/alunos-ativos'+('?' + request.url.query if request.url.query else ''),303)
        if slug not in SLUGS:raise HTTPException(404)
        if formato not in ('','csv'):raise HTTPException(400,'Formato inválido.')
        try:result=result_for(db,slug,fluxo,periodo,busca,ativos=='1')
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        if formato=='csv':return Response(old.csv_content(result),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{slug}.csv"'})
        total=len(result['rows']);pages=max(1,(total+49)//50);page=min(max(1,pagina),pages)
        params={k:v for k,v in dict(fluxo=fluxo,periodo=periodo,busca=busca,ativos=ativos).items() if v}
        back='/consultas-administrativas/'+slug+'?'+urlencode(params|{'pagina':page})
        for row in result['rows']:
            target=('/mobilidade/'+str(row['id']) if slug in ('previsao-retorno-estudantes','alunos-ativos') else '/cadastros-administrativos/'+('parceiras' if slug=='deadline-parceiras' else 'universidades' if slug=='lista-emails-universidades' else slug)+'/'+str(row['id']))
            row['open_url']=target+'?'+urlencode({'voltar':back})
            row['link_items']=link_items(row.get('links',''))
        result.update(rows=result['rows'][(page-1)*50:page*50],total=total,page=page,pages=pages,slug=slug,fluxo=fluxo,periodo=periodo,busca=busca,ativos=ativos,query=urlencode(params))
        return render(request,'admin_queries_v53_25.html',common_context(request,'dashboard')|result)
    @app.get('/cadastros-administrativos/{kind}/{ident}',response_class=HTMLResponse)
    def open_detail(request:Request,kind:str,ident:int,voltar:str='',salvo:str='',db=Depends(get_db)):
        require_view(request,'dashboard');record=detail(db,kind,ident)
        return render(request,'admin_detail_v53_25.html',common_context(request,'dashboard')|dict(record=record,kind=kind,back=safe_back(voltar),csrf=token(request),editable=can_edit(request,'acordos'),saved=salvo=='1',deadlines=json.loads(record.get('deadlines_json','[]')),links=link_items(record.get('links',''))))
    @app.post('/cadastros-administrativos/{kind}/{ident}')
    async def save_detail(request:Request,kind:str,ident:int,db=Depends(get_db)):
        require_view(request,'dashboard')
        if not can_edit(request,'acordos'):raise HTTPException(403,'Sem permissão para editar cadastros administrativos.')
        if not request.cookies.get(cookie_name):raise HTTPException(403)
        body=await request.body()
        if len(body)>150000:raise HTTPException(413,'Formulário muito grande.')
        form=dict(parse_qsl(body.decode('utf-8'),keep_blank_values=True))
        if not hmac.compare_digest(form.get('csrf',''),token(request)):raise HTTPException(403,'Sessão inválida. Reabra a página.')
        record=detail(db,kind,ident)
        try:
            version=int(form.get('version','-1'))
            if version!=record['version']:raise HTTPException(409,'Este cadastro foi alterado em outra janela. Reabra a página antes de salvar.')
            if kind=='projetos':changes={'active':1 if form.get('active')=='1' else 0}
            else:
                changes={k:form.get(k,'').strip() for k in ('contacts','emails')}
                for line in re.split(r'[\s;,]+',changes['emails']):
                    if line and not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',line):raise ValueError('Informe somente endereços de e-mail no campo E-mails.')
                changes['emails']='\n'.join(dict.fromkeys(re.split(r'[\s;,]+',changes['emails'].strip())))
                if kind=='parceiras':
                    changes.update(name=form.get('name','').strip(),links=valid_links(form.get('links','').strip()),notes=form.get('notes','').strip(),active=1 if form.get('active')=='1' else 0)
                    if not changes['name']:raise ValueError('Informe o nome da parceira.')
                    original=json.loads(record['deadlines_json']);entries=[]
                    count=int(form.get('deadline_count','0'))
                    if not 0<=count<=100:raise ValueError('Quantidade de prazos inválida.')
                    for i in range(count):
                        if form.get(f'delete_{i}')=='1':continue
                        label=form.get(f'label_{i}','').strip();value=form.get(f'date_{i}','').strip()
                        if i>=len(original) and not label and not value:continue
                        deadline_date(value,old.local_today())
                        entry=dict(original[i]) if i<len(original) else {'raw':'','source':'Cadastro manual'}
                        entry.update(label=label,value=value);entries.append(entry)
                    changes['deadlines_json']=json.dumps(entries,ensure_ascii=False)
                if any(len(v)>40000 for v in changes.values() if isinstance(v,str)):raise ValueError('Texto muito longo.')
        except (ValueError,UnicodeError) as exc:raise HTTPException(400,str(exc)) from exc
        table,key=TABLES[kind]
        db.execute(text(f'INSERT INTO {table} ({key}) VALUES (:id) ON CONFLICT DO NOTHING'),{'id':ident})
        params=changes|{'id':ident,'version':version}
        updated=db.execute(text(f'UPDATE {table} SET '+','.join(k+'=:'+k for k in changes)+',version=version+1 WHERE '+key+'=:id AND version=:version'),params)
        if updated.rowcount!=1:db.rollback();raise HTTPException(409,'Cadastro alterado em outra janela. Reabra a página.')
        actor=getattr(request.state,'user',None)
        user=(actor.get('username','') if isinstance(actor,dict) else getattr(actor,'username',''))
        db.execute(text('INSERT INTO dari_admin_audit_v53_25 (kind,record_id,username,before_json,after_json) VALUES (:kind,:id,:user,:before,:after)'),dict(kind=kind,id=ident,user=user,before=json.dumps(record,ensure_ascii=False,default=str),after=json.dumps(changes,ensure_ascii=False,default=str)))
        db.commit()
        return RedirectResponse(f'/cadastros-administrativos/{kind}/{ident}?'+urlencode({'voltar':safe_back(form.get('voltar','')),'salvo':'1'}),303)
