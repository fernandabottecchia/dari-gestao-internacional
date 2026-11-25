"""Pares de contato persistidos, com espelho dos campos legados para consultas."""
import json,re
from itertools import zip_longest
from sqlalchemy import text
SPECS={
 'universidades':('dari_university_contacts_v53_25','university_id','contacts','emails'),
 'contatos-universidades':('dari_university_contacts_v53_25','university_id','contacts','emails'),
 'parceiras':('dari_partner_deadlines_v53_25','id','contacts','emails'),
 'projetos':('dari_project_details_v53_26','project_id','foreign_coordinator','email')}

def legacy(names,emails):
    # Do not filter blank lines before pairing: indexes carry the association.
    return [dict(name=n.strip(),email=e.strip()) for n,e in zip_longest((names or '').splitlines(),(emails or '').splitlines(),fillvalue='') if n.strip() or e.strip()]

def attach(db,kind,records):
    if kind not in SPECS:return records
    table,key,names,emails=SPECS[kind]
    stored={r[0]:r[1] for r in db.execute(text(f'SELECT {key},contact_pairs_json FROM {table}'))}
    for r in records:r['contact_pairs44']=json.loads(stored[r['id']]) if stored.get(r['id']) is not None else legacy(r.get(names),r.get(emails))
    return records

def submitted(form,validate=True):
    count=int(form.get('contact_count44','-1'))
    if not 0<=count<=500:raise ValueError('Quantidade de contatos inválida. Reabra o formulário.')
    result=[]
    for i in range(count):
        if form.get(f'contact_delete44_{i}')=='1':continue
        name=form.get(f'contact_name44_{i}','').strip();email=form.get(f'contact_email44_{i}','').strip()
        if not name and not email:continue
        if len(name)>1000 or len(email)>1000 or '\n' in name or '\r' in name:raise ValueError('Contato inválido.')
        if validate and email and not re.fullmatch(r'[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+',email):raise ValueError('Informe um único e-mail válido por contato.')
        result.append(dict(name=name,email=email))
    return result

def changes(kind,items):
    _,_,names,emails=SPECS[kind]
    return {names:'\n'.join(x['name'] for x in items),emails:'\n'.join(x['email'] for x in items),'contact_pairs_json':json.dumps(items,ensure_ascii=False)}
