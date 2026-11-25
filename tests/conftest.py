"""Synthetic fixture only. No student data or credentials from DARI."""
import os,json,sqlite3,tempfile
from pathlib import Path
import pytest

# Optional CI integration destination MUST be a disposable empty PostgreSQL DB.
PG_TEST_URL=os.getenv('DARI_TEST_DATABASE_URL','')
TMP=tempfile.TemporaryDirectory(prefix='dari-tests-');source=Path(TMP.name)/'synthetic.db'
os.environ.update(DARI_ENV='test',DARI_PUBLIC_URL='http://testserver',DARI_SECRET_KEY='synthetic-test-secret-'*4,DARI_FORMS_AUTOSYNC_DISABLED='1')
os.environ['DATABASE_URL']=PG_TEST_URL or ('sqlite:///'+str(source))
ROOT=Path(__file__).resolve().parents[1]
from app.hosting import hash_password

def seed_fixture(path):
    schema=json.loads((ROOT/'deploy/source_schema.json').read_text())
    objects=json.loads((ROOT/'deploy/source_objects.json').read_text())
    with sqlite3.connect(path) as c:
        for sql in schema.values():c.execute(sql)
        for sql in objects.values():c.execute(sql)
        c.execute("INSERT INTO countries(id,name) VALUES (1,'País de teste')")
        c.execute("INSERT INTO universities(id,name,country_id) VALUES (1,'Universidade de teste',1)")
        c.execute("INSERT INTO courses(id,name) VALUES (1,'Engenharia Civil')")
        c.execute("INSERT INTO programs(id,name,code) VALUES (1,'Projeto de teste','TEST')")
        c.execute("INSERT INTO coordinators(id,name) VALUES (1,'Docente de teste')")
        c.execute("INSERT INTO semesters(id,label,year,term) VALUES (1,'2026/2',2026,2)")
        c.execute("INSERT INTO students(id,full_name) VALUES (1,'Estudante Incoming de teste'),(2,'Estudante Outgoing de teste')")
        c.execute("INSERT INTO mobilities(id,direction,student_id,start_semester_id,home_university_id,destination_university_id,course_id,program_id,coordinator_id) VALUES (1,'incoming',1,1,1,NULL,1,1,1),(2,'outgoing',2,1,NULL,1,1,1,1)")
        c.execute("INSERT INTO incoming_details(id,mobility_id) VALUES (1,1)")
        c.execute("INSERT INTO outgoing_details(id,mobility_id) VALUES (1,2)")
        c.execute("INSERT INTO agreements(id,university_id,agreement_type) VALUES (1,1,'Acordo de Cooperação')")
        c.execute("INSERT INTO applications(id,direction,source,status,full_name) VALUES (1,'incoming','synthetic','pending','Candidato de teste')")
        cols=['username','password_hash','display_name','is_admin','is_active','can_view_dashboard','can_view_incoming','can_edit_incoming','can_view_outgoing','can_edit_outgoing','can_view_acordos','can_edit_acordos']
        for u,admin in [('test_admin',True),('test_reader',False)]:
            vals=[u,hash_password('Synthetic-password-1234'),u,admin,True,True,True,admin,True,admin,True,admin]
            c.execute('INSERT INTO user_accounts('+','.join(cols)+') VALUES ('+','.join('?' for _ in vals)+')',vals)
        # Deleted high ID ensures identity reset honors sqlite_sequence, not just MAX(id).
        c.execute("INSERT INTO dari_laboratories_v53_46(id,name) VALUES (100,'Deleted synthetic')")
        c.execute('DELETE FROM dari_laboratories_v53_46 WHERE id=100')
seed_fixture(source)
if PG_TEST_URL:
    from tools.migrate_sqlite import migrate
    migrate(source,PG_TEST_URL,Path(TMP.name)/'migration.json')
else:
    with sqlite3.connect(source) as c:
        c.execute('CREATE TABLE dari_hosting_migration(id INTEGER PRIMARY KEY,release TEXT,migrated_at TEXT,report_json TEXT)')
        c.execute("INSERT INTO dari_hosting_migration VALUES (1,'53.49.0-rc1','','{}')")

@pytest.fixture(scope='session')
def app_module():
    from app import main
    main.startup()
    return main

@pytest.fixture
def client(app_module):
    from starlette.testclient import TestClient
    with TestClient(app_module.app,base_url='http://testserver',headers={'Origin':'http://testserver'}) as c:
        assert c.post('/login',data={'username':'test_admin','password':'Synthetic-password-1234'},follow_redirects=False).status_code==303
        yield c
