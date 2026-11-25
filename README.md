# DARI — edição para hospedagem TIC

**53.49.0-rc1 · candidata à homologação · 25/11/2026**

Aplicação web de gestão de Incoming, Outgoing, Acordos Internacionais, candidaturas e consultas administrativas da DARI/Escola Politécnica/UFRJ. Preparada a partir dos arquivos atuais enviados em 24/11/2026, com interface preservada e adaptação para um banco único PostgreSQL.

Este repositório contém código, testes sintéticos e documentação. O banco com os registros reais, documentos e credenciais deve ser transferido separadamente, pelo canal privado indicado pelo TIC.

## Para a equipe técnica

- [IMPLANTACAO_TIC.md](docs/IMPLANTACAO_TIC.md): ambiente, migração, contas, serviço, HTTPS, Google e backups.
- [HOMOLOGACAO.md](docs/HOMOLOGACAO.md): testes realizados, limites e critérios de liberação.
- [ARQUITETURA.md](docs/ARQUITETURA.md): funcionamento e alterações desta edição.
- [FORMULARIO_TIC.md](docs/FORMULARIO_TIC.md): pedido de infraestrutura e campos do chamado.

Python 3.12, FastAPI 0.136.1, Starlette 1.0.1, Uvicorn 0.52.1, SQLAlchemy 2.1.9 e psycopg 3.3.9. Dependências diretas fixadas em `requirements.txt`. PostgreSQL 16 é a referência dos testes de conversão. A versão efetivamente oferecida pelo TIC deve passar pelos testes de integração.

A aplicação precisa de um processo ASGI permanente, atrás de proxy HTTPS. Há `Dockerfile` e `compose.yaml`, além de exemplo de serviço systemd. **SFTP é um meio de transferência; o serviço Python e o PostgreSQL também precisam ser provisionados.**

## Inicialização resumida — equipe técnica

Em uma instalação Linux, com banco **novo, dedicado e vazio** e variáveis definidas pelo gestor de segredos/arquivo externo protegido:

```bash
python3.12 -m venv /opt/dari/venv
/opt/dari/venv/bin/python -m pip install -r requirements.txt
/opt/dari/venv/bin/python -m tools.migrate_sqlite --sqlite /CAMINHO-PRIVADO/dari_atual.db --report /CAMINHO-PRIVADO/relatorio-migracao.json
/opt/dari/venv/bin/python -m tools.accounts list
/opt/dari/venv/bin/python -m tools.accounts reset --username LOGIN_EXISTENTE
/opt/dari/venv/bin/python -m tools.preflight
/opt/dari/venv/bin/python -m tools.serve
```

Os nomes em maiúsculas são campos a preencher pelo técnico. Nunca colocar a senha na linha de comando. Repetir a redefinição para as contas necessárias; desativar as demais. Ler o guia completo antes da migração definitiva.

A ferramenta recusa um destino que já contenha tabelas, visões ou sequências de aplicação. Não apaga nem mescla o banco antigo. O SQLite é aberto somente para leitura, com snapshot consistente, e todos os dados são conferidos antes do commit.

## Testes

```bash
python -m pip install -r requirements-test.txt
python -m pytest tests -q
```

Sem configuração adicional, usa somente uma base SQLite sintética temporária. Para a integração PostgreSQL, definir `DARI_TEST_DATABASE_URL` com uma base **descartável e vazia**, exclusiva para testes. A ferramenta de migração real é executada antes dos testes. A automação em `.github/workflows/tests.yml` fornece esse banco no GitHub Actions; a execução remota ainda deve ser conferida pelo responsável técnico.

Esta edição não deve ser tratada como produção homologada até concluir o roteiro de homologação no ambiente do TIC.
