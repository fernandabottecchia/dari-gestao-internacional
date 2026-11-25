import os
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL=os.getenv('DATABASE_URL','')
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL é obrigatório. Nenhum banco de demonstração será criado.')
url=make_url(DATABASE_URL)
if url.drivername=='postgresql':
    url=url.set(drivername='postgresql+psycopg')
if os.getenv('DARI_ENV','production')!='test' and url.get_backend_name()!='postgresql':
    raise RuntimeError('Hospedagem exige PostgreSQL. SQLite é permitido apenas nos testes.')
engine=create_engine(url, pool_pre_ping=True, hide_parameters=True, future=True,
    connect_args={'check_same_thread':False} if url.get_backend_name()=='sqlite' else {'connect_timeout':10})
SessionLocal=sessionmaker(bind=engine,autoflush=False,autocommit=False,future=True)
class Base(DeclarativeBase):pass

def get_db():
    with SessionLocal() as db:
        yield db
