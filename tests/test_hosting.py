import hashlib,hmac,os,time,json
import pytest
from sqlalchemy import text,select
from app.hosting import settings,issue_session,read_session

def test_session_expiry_and_password_revocation(monkeypatch):
    token=issue_session('test','hash1')
    assert read_session(token,'hash1')['user']=='test'
    assert not read_session(token,'hash2')
    assert not read_session(token+'tampered','hash1')
    now=time.time();monkeypatch.setattr(time,'time',lambda:now+8*3600+1)
    assert not read_session(token,'hash1')

def test_all_main_pages(client):
    routes=['/','/incoming','/outgoing','/acordos','/configuracoes?sec=calendario','/configuracoes?sec=desenvolvimento','/mobilidade/1','/mobilidade/2/editar','/acordos/item/1/editar','/incoming/documentos','/outgoing/documentos','/acordos/documentos','/candidaturas/incoming/pendentes','/candidaturas/outgoing/rejeitadas','/candidaturas/fontes?direcao=incoming']
    routes += ['/consultas-administrativas/'+s for s in ['projetos','universidades','professores','cursos','coordenadores-cursos','deadline-parceiras','lista-emails-universidades','laboratorios','previsao-retorno-estudantes','alunos-ativos','inicio-intercambio']]
    for route in routes:assert client.get(route).status_code==200,route

def test_origin_and_authorization(client,app_module):
    assert client.post('/configuracoes/calendario/salvar',data={},headers={'Origin':'https://evil.invalid'}).status_code==403
    assert client.get('/',headers={'Host':'evil.invalid'}).status_code==400
    assert client.get('/openapi.json').status_code==404
    client.get('/logout')
    client.post('/login',data={'username':'test_reader','password':'Synthetic-password-1234'})
    assert client.get('/configuracoes').status_code==403
    assert client.post('/acordos/item/1/acoes',data={'next_step':'blocked'}).status_code==403

@pytest.mark.parametrize('mid',[1,2])
def test_mobility_duration_and_status(client,app_module,mid):
    data=dict(name='Estudante de teste',course_id=1,program_id=1,coordinator_id=1,university_id=1,semester_label='2026/2',mobility_type='Duplo Diploma',duration_semesters='4',status_choice='active')
    r=client.post(f'/mobilidade/{mid}/editar',data=data,follow_redirects=False)
    assert r.status_code==303,r.text
    from app.models import Mobility
    from app.mobility_schedule_v53_40 import context
    from app.mobility_workflow_v53_16 import state_of
    with app_module.SessionLocal() as db:
        m=db.get(Mobility,mid)
        assert m.duration_semesters==4
        assert context(db,m)['forecast40']['return_period']=='2028/2'
        assert state_of(db,mid)=='active'
    data['duration_semesters']='';data['status_choice']='unclassified'
    assert client.post(f'/mobilidade/{mid}/editar',data=data,follow_redirects=False).status_code==303
    with app_module.SessionLocal() as db:
        assert db.get(Mobility,mid).duration_semesters is None
        assert state_of(db,mid) is None

def test_calendar_and_date_mode(client,app_module):
    for sem,start,end in [('2029/1','2029-01-01','2029-06-30'),('2029/2','2029-07-01','2029-12-31')]:
        r=client.post('/configuracoes/calendario/salvar',data=dict(semester=sem,start_date=start,end_date=end),follow_redirects=False)
        assert r.status_code==303
    data=dict(name='Estudante de teste',course_id=1,program_id=1,coordinator_id=1,university_id=1,period_mode='dates',mobility_start_date='2029-03-01',mobility_end_date='2029-09-01',mobility_type='Estágio',status_choice='active')
    assert client.post('/mobilidade/2/editar',data=data,follow_redirects=False).status_code==303
    from app.models import Mobility
    from app.mobility_schedule_v53_40 import context
    with app_module.SessionLocal() as db:
        m=db.get(Mobility,2);f=context(db,m)['forecast40']
        assert f['return_period']=='2029/2'
        assert f['expected'].isoformat()=='2029-09-01'
    assert '2029/2' in client.get('/consultas-administrativas/previsao-retorno-estudantes').text

def admin_form(client,**data):
    data.setdefault('csrf',hmac.new(settings.secret.encode(),('admin28:'+client.cookies.get('dari_session')).encode(),hashlib.sha256).hexdigest())
    data.setdefault('version','0');data.setdefault('contact_count44','0')
    return data

@pytest.mark.parametrize('kind,name',[('projetos','Projeto novo'),('universidades','Universidade nova'),('professores','Professor novo'),('parceiras','Parceira nova'),('laboratorios','Laboratório novo')])
def test_administrative_create_edit_delete(client,app_module,kind,name):
    r=client.post(f'/cadastros-administrativos/{kind}/novo',data=admin_form(client,name=name),follow_redirects=False)
    assert r.status_code==303,r.text[:300]
    url=r.headers['location'].split('?')[0]
    ident=int(url.rsplit('/',1)[-1])
    if kind=='laboratorios':assert ident>100
    page=client.get(url+'?editar=1');assert page.status_code==200
    from app.admin_queries_v53_28 import detail
    with app_module.SessionLocal() as db:version=detail(db,kind,ident)['version']
    r=client.post(url,data=admin_form(client,name=name+' editado',version=str(version)),follow_redirects=False)
    assert r.status_code==303,r.text[:300]
    # Optimistic lock rejects a stale edit.
    r=client.post(url,data=admin_form(client,name='stale',version=str(version)),follow_redirects=False)
    assert r.status_code==409
    with app_module.SessionLocal() as db:version=detail(db,kind,ident)['version']
    r=client.post(url+'/excluir',data=admin_form(client,version=str(version),confirm_delete='1'),follow_redirects=False)
    assert r.status_code==303,r.text[:300]

def test_agreement_actions_and_documents(client,app_module):
    r=client.post('/acordos/item/1/acoes',data={'next_step':'Teste de acompanhamento'},follow_redirects=False)
    assert r.status_code==303
    from app.models import AgreementAction
    with app_module.SessionLocal() as db:aid=db.scalar(select(AgreementAction.id).where(AgreementAction.agreement_id==1))
    assert client.post(f'/acordos/item/1/acoes/{aid}/alternar-conclusao',follow_redirects=False).status_code==303
    assert client.post(f'/acordos/item/1/acoes/{aid}/editar',data={'next_step':'Atualizado'},follow_redirects=False).status_code==303
    assert client.post(f'/acordos/item/1/acoes/{aid}/excluir',follow_redirects=False).status_code==303
    r=client.post('/incoming/documentos/enviar',files={'arquivo':('teste.txt',b'DARI synthetic file','text/plain')},follow_redirects=False)
    assert r.status_code==303
    from app.document_repository_v53_47 import StoredDocument
    with app_module.SessionLocal() as db:
        item=db.scalar(select(StoredDocument).where(StoredDocument.path=='teste.txt'))
        assert item.body==b'DARI synthetic file'

def test_migration_refuses_nonempty_database():
    from conftest import PG_TEST_URL,source,TMP
    if not PG_TEST_URL:pytest.skip('Run in PostgreSQL CI/staging')
    from tools.migrate_sqlite import migrate
    from pathlib import Path
    with pytest.raises(ValueError,match='não está vazio'):
        migrate(source,PG_TEST_URL,Path(TMP.name)/'must-not-exist.json')
