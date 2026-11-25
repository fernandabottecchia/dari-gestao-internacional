"""Cadastros compartilhados e resolução de nomes atuais sem alterar fontes históricas."""
import json,re
from sqlalchemy import text
from .university_reference_v53_20 import key,display

def university_label(db,value):
    if not value:return value
    found=db.execute(text('SELECT u.name FROM dari_university_labels_v53_28 a JOIN universities u ON u.id=a.university_id WHERE a.label_key=:key'),{'key':key(value)}).scalar()
    return found or value

def remember(db,ident,value):
    if not value:return
    k=key(value)
    existing=db.execute(text('SELECT university_id FROM dari_university_labels_v53_28 WHERE label_key=:key'),{'key':k}).scalar()
    if existing is not None and existing!=ident:raise ValueError('Este nome já identifica outra universidade. Use o cadastro existente.')
    db.execute(text('INSERT INTO dari_university_labels_v53_28(label_key,university_id) VALUES (:key,:id) ON CONFLICT DO NOTHING'),{'key':k,'id':ident})

def ensure_named(db,table,name):
    if table not in ('countries','coordinators','courses'):raise ValueError('Cadastro inválido.')
    name=name.strip()
    if table=='courses':
        from .course_names_v53_39 import canonical as canonical_course
        name=canonical_course(name)
    if not name or len(name)>180:raise ValueError('Informe um nome válido.')
    for ident,current in db.execute(text(f'SELECT id,name FROM {table}')):
        if key(current)==key(name):
            if table=='coordinators' and db.execute(text("SELECT 1 FROM dari_admin_deleted_v53_27 WHERE kind='professores' AND record_id=:id"),{'id':ident}).scalar():raise ValueError('Esse professor está excluído. Informe outro cadastro.')
            return ident
    return db.execute(text(f'INSERT INTO {table}(name) VALUES (:name) RETURNING id'),{'name':name}).scalar_one()

def ensure_university(db,name,acronym='',country_id=None):
    name=display(name.strip(),acronym.strip())
    if not name or len(name)>255 or len(acronym)>60:raise ValueError('Informe nome e sigla válidos para a universidade.')
    ident=db.execute(text('SELECT university_id FROM dari_university_labels_v53_28 WHERE label_key=:key'),{'key':key(name)}).scalar()
    if ident is not None:return ident
    for uid,current in db.execute(text('SELECT id,name FROM universities')):
        if key(current)==key(name):remember(db,uid,current);return uid
    if country_id and not db.execute(text('SELECT id FROM countries WHERE id=:id'),{'id':country_id}).scalar():raise ValueError('País inválido.')
    ident=db.execute(text('INSERT INTO universities(name,acronym,country_id) VALUES (:name,:acronym,:country_id) RETURNING id'),{'name':name,'acronym':acronym or None,'country_id':country_id}).scalar_one()
    remember(db,ident,name);return ident

def rename_university(db,ident,name,acronym,country_id):
    old=db.execute(text('SELECT name,acronym FROM universities WHERE id=:id'),{'id':ident}).first()
    if not old:raise ValueError('Universidade não encontrada.')
    # O formulário mostra o nome completo atual; trocar só a sigla deve substituir o sufixo anterior.
    name=name.strip()
    if old[1]:name=re.sub(r'\s[-–—]\s'+re.escape(old[1])+r'$', '', name, flags=re.I).strip()
    name=display(name,acronym.strip())
    if not name or len(name)>255 or len(acronym)>60:raise ValueError('Nome ou sigla inválidos.')
    remember(db,ident,old[0]);remember(db,ident,name)
    db.execute(text('UPDATE universities SET name=:name,acronym=:acronym,country_id=:country WHERE id=:id'),{'name':name,'acronym':acronym or None,'country':country_id,'id':ident})
    for pid,label in db.execute(text('SELECT id,name FROM dari_partner_deadlines_v53_25 WHERE university_id IS NULL')):
        linked=db.execute(text('SELECT university_id FROM dari_university_labels_v53_28 WHERE label_key=:key'),{'key':key(label)}).scalar()
        if linked==ident:db.execute(text('UPDATE dari_partner_deadlines_v53_25 SET university_id=:uid WHERE id=:id'),{'uid':ident,'id':pid})
    # A parceira vinculada representa esta instituição; contatos e prazos permanecem.
    db.execute(text('UPDATE dari_partner_deadlines_v53_25 SET name=:name,version=version+1 WHERE university_id=:id'),{'name':name,'id':ident})
    return name

def choices(db,table,column,defaults=()):
    allowed={('mobilities','mobility_type'),('agreements','agreement_type'),('agreements','progress'),('agreements','ufrj_stage'),('agreements','status')}
    if (table,column) not in allowed:raise ValueError('Campo inválido.')
    from .mobility_periods_v53_29 import modality
    return sorted({modality(v) for v in defaults}|{modality(str(v).strip()) for v, in db.execute(text(f'SELECT DISTINCT {column} FROM {table}')) if v and str(v).strip()},key=__import__(__package__+".editing_v53_30",fromlist=["sort_key"]).sort_key)
