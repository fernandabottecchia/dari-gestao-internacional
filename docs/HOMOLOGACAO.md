# Validação e critérios de liberação

**25/11/2026 — 53.49.0-rc1: candidata à homologação.**

## Verificações concluídas na preparação

| Verificação | Resultado e alcance |
| --- | --- |
| Origem SQLite | Snapshot consistente, integridade e vínculos conferidos, sem alteração da base original. |
| Conversão com os dados recebidos | 151 tabelas e 473.350 registros carregados em PostgreSQL 16.2 isolado. Contagens e hashes do conteúdo de todas as tabelas correspondem à origem. |
| Estrutura | Tabelas, chaves, índices, tipos, sequências e conversão das regras de normalização exercitados no PostgreSQL. |
| Consultas da interface | 31 páginas responderam HTTP 200 sobre uma cópia de teste do SQLite atual. 3.061 consultas SELECT capturadas dessas páginas foram compiladas para PostgreSQL e aceitas por EXPLAIN no banco convertido. |
| Escritas PostgreSQL | Script transacional isolado exercitou cadastros, atualização, exclusão lógica, normalização, status, calendário, acompanhamento de acordos e bytes de documento; concluído sem erro e revertido. |
| Testes automatizados da aplicação | 12 casos passaram com base sintética temporária: sessão/expiração, origem/Host/permissões, telas, duração/status, datas/calendário, CRUDs administrativos, concorrência de versão, ações de acordos e documento. |
| Recusa de banco de destino preenchido | Teste automatizado fornecido; sua execução exige PostgreSQL em rede e ficou pendente neste ambiente. |
| Preparador de entrega | Conferência de versão/payload, snapshot e separação código/dados testados programaticamente; recusa código divergente sem modificar a instalação. O duplo clique do .cmd precisa ser confirmado no Windows da unidade. |

Os números das páginas e consultas descrevem o conjunto exercitado; não significam cobertura de todas as combinações possíveis da aplicação.

## Limites desta validação

O ambiente de preparação não permite abrir sockets de servidor. O PostgreSQL foi executado em modo isolado, sem rede, para validar estrutura, carga e SQL. A suíte HTTP utilizou SQLite de teste. **O caminho completo FastAPI → SQLAlchemy/psycopg → PostgreSQL em rede ainda não foi executado aqui.**

A ferramenta real de migração, o processo em contêiner/systemd, o proxy HTTPS, as permissões do TIC, as credenciais Google e a restauração de backup precisam ser testados no ambiente de homologação. Não houve acesso ao computador da unidade, ao PostgreSQL antigo, ao servidor TIC ou publicação no GitHub. Não se afirma que a implantação esteja concluída.

## Teste automatizado em PostgreSQL

Há uma automação GitHub Actions em `.github/workflows/tests.yml`, com PostgreSQL 16 descartável e dados exclusivamente sintéticos. Ela executa a ferramenta de migração e a suíte da aplicação. Não usa nem solicita dados reais ou credenciais institucionais. Depois do primeiro push, o responsável deve conferir a aba Actions e corrigir qualquer falha antes de avançar.

Para executar fora do GitHub, criar banco de teste vazio e definir `DARI_TEST_DATABASE_URL` para ele, então:

```bash
python -m pip install -r requirements-test.txt
python -m pytest tests -q
```

O teste não apaga bancos existentes para limpar o ambiente. Cada execução PostgreSQL precisa de um banco vazio novo. O teste de recusa do destino preenchido utiliza o próprio banco sintético depois da primeira carga, e verifica que uma nova tentativa é interrompida.

## Homologação no TIC com a cópia real

1. Confirmar que a execução é na base de homologação, derivada do SQLite atual. Verificar relatório integral e contagens antes de permitir edições de teste.
2. Redefinir as contas e conferir login, expiração, sair, acesso de leitura e acesso de edição. Verificar HTTPS, cookie Secure, domínio e cabeçalhos do proxy.
3. Abrir visão geral, Incoming, Outgoing, Acordos, candidaturas e todas as consultas administrativas. Conferir alguns casos conhecidos com a equipe, usando os mesmos filtros da instalação local.
4. Em fichas de teste, alterar início/duração: 2026/2 mais 4 semestres deve retornar em 2028/2. Duração vazia deve continuar vazia. Testar modo por datas, ajuste no calendário e atualização da previsão de retorno.
5. Criar, editar e excluir cadastros de teste de universidades/contatos, projetos, parceiros/prazos, laboratórios, professores, cursos e coordenadores. Conferir bloqueio de edição desatualizada em duas abas nos cadastros que já têm esse controle.
6. Testar acompanhamento de acordos, status dos estudantes, exclusões administrativas e filtros. Conferir exportações XLSX/PDF e cartas de aceite geral/laboratório para Incoming.
7. Enviar, baixar, renomear e excluir arquivo sintético nos repositórios e na ficha do aluno. Reiniciar o processo e confirmar persistência. Testar o limite de upload.
8. Com credenciais Google autorizadas e somente leitura, habilitar a integração e conferir candidaturas e anexos. Simular indisponibilidade/permissão negada e verificar o erro apresentado, sem alterar planilhas originais.
9. Ensaiar backup e restauração em outro banco e conferir novamente dados/documentos. Verificar logs e capacidade para o uso esperado.
10. Registrar aceite do gestor e técnico; planejar a carga final, após parada das edições locais. Não promover o banco alterado por testes como produção oficial.

A existência de um endpoint de saúde ou de um teste aprovado não substitui os demais critérios. A decisão de liberação deve usar o resultado do ambiente institucional e a conferência funcional da DARI.
