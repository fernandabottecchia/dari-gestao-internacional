# Arquitetura e escopo

## Funcionamento após a implantação

A equipe abre o endereço HTTPS do DARI no navegador, na UFRJ ou em casa, conforme a política de acesso externo aprovada. O processo Python e o banco ficam no servidor administrado pelo TIC. Não é necessário ligar o servidor no computador da DARI nem instalar PostgreSQL nas máquinas dos usuários.

O navegador envia as ações ao FastAPI, que autentica a conta, verifica suas permissões e grava no único PostgreSQL configurado em `DATABASE_URL`. A edição de hospedagem encerra a inicialização se essa configuração estiver ausente ou apontar para SQLite; não cria um banco de demonstração como substituto. Falha de conexão interrompe a operação, sem gravar em uma cópia local.

A consulta offline com sincronização de cópias locais não faz parte desta entrega. A instalação local anterior continua disponível durante a homologação, mas suas edições não são sincronizadas com o novo servidor. No corte definitivo, deve haver uma única origem oficial de gravação.

## Alterações nesta edição

- Introspecção de tabelas compatível com PostgreSQL, substituindo consultas a `sqlite_master` nas rotinas operacionais.
- Inserções com retorno de identificador e tratamento explícito de conflitos, compatíveis com PostgreSQL.
- Calendário, acompanhamento de acordos e documentos passam a usar diretamente o banco da aplicação. O cliente do servidor central antigo fica desabilitado.
- Inicialização sem importação histórica, criação de usuários padrão, reconciliação automática ou semeadura de demonstração. A configuração de fontes Google vem do banco migrado.
- Segredo de sessão obrigatório e externo ao código; sessões com validade de oito horas, invalidadas pela troca da senha; cookies seguros em produção, verificação de origem das gravações, validação de Host e limitação de tentativas de login.
- O processo recusa iniciar se houver conta ativa com a senha antiga padrão ou não houver administrador ativo. O técnico redefine as contas explicitamente.
- Dados pessoais, bancos, planilhas, tokens, ambientes virtuais, instaladores antigos e backups não integram o repositório.
- Ajustes de compatibilidade nos cadastros administrativos para datas recebidas do PostgreSQL e para não tentar recriar registros principais ao editar/excluir.

## Dados preservados

A fonte desta preparação é o SQLite enviado em `HISTORICO_CONSOLIDADO(8).rar`. Contagens brutas da cópia analisada:

| Entidade | Registros |
| --- | ---: |
| Estudantes | 4.076 |
| Mobilidades | 4.096 |
| Incoming | 1.496 |
| Outgoing | 2.599 |
| Mobilidade histórica a classificar | 1 |
| Acordos | 79 |
| Candidaturas | 757 |
| Universidades | 776 |
| Contas | 4 |
| Todas as 151 tabelas de dados | 473.350 |

Esses números são inventário do banco, não totais esperados de cada card: filtros, exclusões administrativas e critérios operacionais continuam aplicados. A migração não reclassifica o caso histórico pendente nem altera homologações.

O SQLite contém ainda `sqlite_sequence` e `sqlite_stat1`: estruturas internas, não entidades de negócio. A migração respeita os valores de sequência para impedir reutilização de identificadores excluídos. As estatísticas internas não são transportadas.

## Documentos e Google

O banco analisado contém 1.061 referências de anexos de formulários, todas com registro de erro de obtenção e sem conteúdo binário armazenado. O repositório documental tem 12 pastas e um registro de arquivo excluído, também sem conteúdo. As pastas de documentos enviadas estavam vazias, e não havia caminhos locais de anexos de candidaturas.

A migração preserva referências, metadados e erros. Ela não consegue transportar conteúdo que não existe nos arquivos fornecidos. Após autorizar o Google no servidor, verificar a recuperação dos anexos necessários. Arquivos removidos do Drive ou sem permissão de leitura continuarão indisponíveis até resolução pela equipe responsável.

A integração usa escopos Google Sheets/Drive somente de leitura. As credenciais não estão neste repositório e não são incluídas automaticamente na cópia privada do preparador. Nenhuma planilha original é alterada pela migração.

## Migração

`tools.migrate_sqlite` compara o esquema, índices e gatilhos de origem com a versão analisada. Havendo mudança de estrutura, interrompe para revisão. Novos registros e edições na mesma estrutura são aceitos.

A conversão preserva IDs, vínculos, valores nulos, textos, datas, flags, históricos, exclusões lógicas e bytes. Campos VARCHAR são representados por TEXT, pois SQLite não impõe os limites de comprimento declarados; nenhum texto é cortado. INTEGER é convertido para BIGINT. Tipos inválidos provocam interrupção, sem correção silenciosa.

São verificadas as contagens e somas de hashes SHA-256 de todas as linhas de cada tabela, sem depender da ordem. As restrições de vínculo são verificadas antes da confirmação. As 24 regras SQLite de normalização são representadas por funções e oito gatilhos PostgreSQL equivalentes, instalados depois da conferência do conteúdo original.

A operação ocorre em uma transação no destino. O banco de origem permanece somente leitura. O relatório local e `dari_hosting_migration` registram a versão e o resultado; não armazenam dados pessoais de linhas no relatório.
