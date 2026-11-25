"""Run on the TIC staging environment before exposing the service."""
from app.database import engine
from app.hosting import validate_database

def main():
    validate_database(engine)
    print('Configuração, PostgreSQL, migração e contas iniciais verificados.')
    print('Concluir também os testes de interface e restauração descritos em docs/HOMOLOGACAO.md.')
if __name__=='__main__':main()
