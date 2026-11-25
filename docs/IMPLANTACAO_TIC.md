# Implantação pelo responsável técnico, em conjunto com o TIC

## 1. Ambiente a confirmar

Provisionar Linux com Python 3.12 e processo ASGI permanente, ou contêiner construído pelo Dockerfile. A referência de banco é PostgreSQL 16. O serviço web escuta a porta interna 8000; somente o proxy HTTPS deve alcançá-la. O banco deve ser acessível apenas pelo serviço e pelos administradores autorizados, sem exposição direta aos navegadores.

Ponto inicial sugerido para avaliação, não medição de capacidade: 2 vCPU, 4 GiB de RAM e armazenamento dimensionado para banco, anexos, temporários e backups. O SQLite recebido tem aproximadamente 104 MB; a expansão e o crescimento no PostgreSQL devem ser medidos na homologação. Download de anexos Google pode aumentar significativamente o armazenamento. Exportações e relatórios precisam ser exercitados com volume real.

Disponibilizar DNS/certificado HTTPS, canal privado de transferência, contas de manutenção e políticas de backup/retenção. Se o serviço disponibilizado for apenas armazenamento SFTP ou execução PHP, solicitar ambiente compatível com Python ASGI; essa diferença deve ser resolvida antes da implantação.

Usar domínio na raiz do site, por exemplo `https://DOMINIO-APROVADO`, sem subcaminho `/dari`: os templates existentes utilizam URLs iniciadas por `/`.

## 2. Código e configuração

Receber o repositório privado. Não copiar o ambiente virtual `.venv` do Windows. Instalar dependências no servidor novo:

```bash
python3.12 -m venv /opt/dari/venv
/opt/dari/venv/bin/python -m pip install -r requirements.txt
```

Ou construir a imagem:

```bash
docker build -t dari:53.49.0-rc1 .
```

A compilação Docker não foi executada neste ambiente de preparação; deve integrar a homologação do TIC, se essa opção for escolhida.

Definir as variáveis de `.env.example` em arquivo externo protegido ou gestor de segredos. Os comandos Python não leem `.env` automaticamente; executar as ferramentas em um shell com as variáveis já exportadas ou no contêiner configurado pelo TIC. Não registrar o valor de `DATABASE_URL` ou segredos no chamado/log de implantação.

Gerar o segredo de sessão no servidor (armazenar diretamente no gestor de segredos/arquivo protegido):

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Esse comando exibe o segredo no terminal privado; não incluir sua saída em prints ou documentos. Usar `DARI_ENV=production`, URL HTTPS oficial e `DARI_FORMS_AUTOSYNC_DISABLED=1` inicialmente. Jamais usar `DARI_ENV=test` no serviço publicado.

`DATABASE_URL` utiliza o formato SQLAlchemy `postgresql+psycopg://...`. Configurar criptografia/verificação de certificado do banco conforme a infraestrutura. Para conexão remota, preferir `sslmode=verify-full` com a CA institucional apropriada. Codificar caracteres reservados da senha na URI; não inventar nem reutilizar as credenciais do PostgreSQL antigo.

## 3. Primeiro ensaio, sem alterar a instalação usada pela equipe

Criar **um banco dedicado vazio de homologação**. O executor da migração precisa criar tabelas, índices, sequências, funções e gatilhos nesse banco. A ferramenta recusa destinos com objetos de aplicação, inclusive em outros schemas. Não passar a URL do banco antigo.

Receber `dari_atual.db` por SFTP ou outro canal privado aprovado. Não colocá-lo no diretório público da aplicação. Comparar seu SHA-256 com `manifesto_privado.json`, gerado pelo preparador. Em Linux:

```bash
sha256sum /CAMINHO-PRIVADO/dari_atual.db
```

Validar a origem e migrar (as duas opções fazem snapshot consistente do SQLite e não alteram o arquivo original):

```bash
python -m tools.migrate_sqlite --sqlite /CAMINHO-PRIVADO/dari_atual.db --report /CAMINHO-PRIVADO/origem.json --check-only
python -m tools.migrate_sqlite --sqlite /CAMINHO-PRIVADO/dari_atual.db --report /CAMINHO-PRIVADO/migracao.json
```

O destino é obtido exclusivamente de `DATABASE_URL`. O relatório de migração deve indicar `counts_verified: true` e `contents_verified: true`, com todas as tabelas. O SHA-256 desse relatório corresponde ao snapshot criado pela ferramenta e pode diferir do arquivo recebido por detalhes físicos do SQLite; a conferência do arquivo recebido usa o manifesto privado. Comparações de dados usam hashes de conteúdo por tabela.

Se ocorrer erro, a transação PostgreSQL é revertida. Não tentar contornar a recusa apagando tabelas de um banco existente. Investigar a causa, confirmar o destino e provisionar um banco vazio quando apropriado. O console pode conter informações técnicas da falha: encaminhar logs somente ao responsável autorizado.

As tabelas de entidades, importações e históricos são preservadas. Tipos incompatíveis, esquema divergente, vínculos inválidos ou diferenças na conferência interrompem o processo.

## 4. Contas e permissões

As quatro contas existentes são migradas com seus hashes e permissões. A publicação não pode conservar a senha antiga padrão. Listar as contas no terminal privado e redefinir individualmente, ou desativar as que não serão usadas:

```bash
python -m tools.accounts list
python -m tools.accounts reset --username LOGIN_EXISTENTE
python -m tools.accounts disable --username LOGIN_NAO_UTILIZADO
```

As senhas são solicitadas sem eco e devem ter ao menos 12 caracteres. Para um administrador novo, criado explicitamente:

```bash
python -m tools.accounts reset --username NOVO_ADMIN --create-admin
```

Depois da carga, a credencial do serviço pode ser uma role diferente da credencial de migração. Conceder apenas CONNECT ao banco, USAGE ao schema, SELECT/INSERT/UPDATE/DELETE às tabelas, USAGE/SELECT/UPDATE às sequências e execução das funções necessárias. O processo web não precisa de superusuário nem de permissão de criar tabelas. Preservar a role proprietária para futuras migrações aprovadas. Conferir acesso aos objetos criados após cada atualização de esquema.

## 5. Serviço e HTTPS

Executar `python -m tools.preflight`. Se aprovado, usar uma das opções:

- **systemd**: adaptar `deploy/dari.service.example`, usuário dedicado, venv Linux, código em `/opt/dari/current` e configuração protegida em `/etc/dari/dari.env`.
- **Contêiner**: adaptar `compose.yaml`, configurar `.env` fora do Git e iniciar a imagem. O Compose fornecido contém apenas a aplicação; não cria nem apaga PostgreSQL.

Usar um processo/worker inicialmente para não duplicar o leitor automático de planilhas. Não habilitar `--reload`. O proxy reverso pode ser Nginx ou equivalente do TIC. `deploy/nginx.conf.example` mostra certificado, limites de upload e cabeçalhos. O TIC deve preencher caminhos reais, instalar o arquivo de cabeçalhos indicado e validar sua configuração.

Configurar `DARI_FORWARDED_ALLOW_IPS` com os endereços reais do proxy vistos pelo Uvicorn. No contêiner, `127.0.0.1` pode não ser o endereço visto para o Nginx do host; ajustar após verificar a rede. Não confiar em `*` com acesso público. O proxy deve substituir os cabeçalhos encaminhados e ser o único caminho público até o serviço.

`DARI_PUBLIC_URL` deve corresponder exatamente ao endereço usado nos formulários, inclusive porta se houver. Requests de gravação com origem diferente são recusadas. O domínio precisa estar disponível por HTTPS antes do teste de login de produção, porque o cookie é Secure.

Endpoints sem dados pessoais: `/health/live` (processo) e `/health/ready` (banco e marcador da migração). Verificar também o login e as operações reais do roteiro; healthcheck não substitui homologação.

## 6. Google e anexos

Não transportar os arquivos `central_config` para o GitHub. Configurar credenciais autorizadas em diretório externo; conta de serviço requer compartilhamento de leitura das planilhas e arquivos necessários. Alternativamente, usar OAuth com escopos somente leitura e consentimento da conta responsável. A escolha depende da política da unidade/TIC.

Variáveis e nomes reconhecidos: `DARI_GOOGLE_CREDENTIALS_DIR`, `DARI_FORMS_SERVICE_ACCOUNT_FILE`, `DARI_FORMS_OAUTH_CLIENT` e `DARI_FORMS_TOKEN`. Os padrões de nomes são `google_forms_service_account.json`, `google_oauth_client.json` e `google_forms_readonly_token.json`. Credenciais de conta de serviço podem ser montadas somente leitura; o fluxo OAuth atual precisa poder atualizar seu arquivo de token, em diretório gravável restrito.

Liberar saída HTTPS para os serviços Google usados e manter a leitura automática desligada até a validação. Ao habilitar `DARI_FORMS_AUTOSYNC_DISABLED=0`, reiniciar o processo e conferir fontes, permissões, candidaturas e anexos. Nenhuma escrita nas planilhas é autorizada por esta entrega.

A amostra enviada contém referências de anexos sem bytes disponíveis; detalhes em ARQUITETURA.md. As credenciais não garantem recuperação de arquivo que tenha sido removido ou esteja sem permissão.

## 7. Corte definitivo

Durante a homologação, a equipe pode continuar trabalhando no SQLite local. O ensaio hospedado não será atualizado automaticamente com essas mudanças.

Combinar uma janela de parada de edição. Encerrar o DARI local, executar novamente o preparador com o SQLite mais recente e transferir o novo snapshot. Migrar para **outro banco de produção vazio**, conferir relatório, contas, telas e anexos, e então liberar o endereço oficial. Não usar o banco de ensaio alterado em testes como produção.

Depois do corte, a equipe grava somente no endereço hospedado. Conservar a instalação local e os snapshots como histórico protegido. Não continuar editando alternadamente em casa, no SQLite do laboratório e no servidor: esta versão não faz fusão dessas bases.

## 8. Backup, restauração e atualizações

Combinar com o TIC periodicidade, retenção, responsável por restauração e prazo aceitável de perda de dados. Incluir PostgreSQL e credenciais/configurações externas em política institucional apropriada. Os novos documentos enviados pela interface ficam no PostgreSQL e acompanham seu backup.

Exemplo para operador com `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE` e autenticação privada já configurados. Usar versão de `pg_dump` compatível com a versão do servidor; não usar a URI SQLAlchemy com `+psycopg` no CLI PostgreSQL.

```bash
pg_dump --format=custom --file=/BACKUP-PRIVADO/dari.dump
pg_restore --list /BACKUP-PRIVADO/dari.dump
```

A listagem não prova restauração. Testar em um banco novo e isolado de recuperação, selecionado explicitamente pelo operador:

```bash
pg_restore --exit-on-error --no-owner --dbname=BANCO_NOVO_DE_RESTAURACAO /BACKUP-PRIVADO/dari.dump
```

Aplicar permissões adequadas no banco restaurado e verificar aplicação, contagens e documentos. Nunca restaurar por cima do banco em uso durante esse ensaio.

Para atualizações futuras, manter dados e segredos fora do checkout, fixar tag/commit, testar em homologação e gerar backup antes de mudanças de esquema. A importação inicial SQLite não deve ser repetida sobre a produção em uso. A reversão de uma atualização de código depende da compatibilidade do esquema; uma restauração de backup pode descartar gravações posteriores e exige planejamento do responsável.

## Referências técnicas

- FastAPI atrás de proxy: https://fastapi.tiangolo.com/advanced/behind-a-proxy/
- HTTPS: https://fastapi.tiangolo.com/deployment/https/
- Contêiner: https://fastapi.tiangolo.com/deployment/docker/
- PostgreSQL pg_dump: https://www.postgresql.org/docs/16/app-pgdump.html
- PostgreSQL pg_restore: https://www.postgresql.org/docs/16/app-pgrestore.html
