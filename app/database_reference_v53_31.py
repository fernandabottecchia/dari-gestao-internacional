"""Read-only schema reference, requested only by the administrator settings page."""
from sqlalchemy import inspect

DESCRIPTIONS = {
 'students': 'Identidade do estudante: nome, documentos e contato. Um estudante pode ter mais de uma mobilidade.',
 'mobilities': 'Processos Incoming e Outgoing. Liga o estudante ao curso, programa, coordenador, universidades e semestres de início e término.',
 'universities': 'Cadastro de instituições. A mesma universidade pode participar de mobilidades e de vários acordos.',
 'countries': 'Países vinculados às universidades.',
 'courses': 'Cursos associados aos processos de mobilidade.',
 'coordinators': 'Coordenadores associados às mobilidades.',
 'programs': 'Programas e projetos associados às mobilidades.',
 'semesters': 'Catálogo de semestres: rótulo, ano e período. É diferente da evidência de presença do aluno em cada semestre.',
 'incoming_details': 'Informações complementares do Incoming, vinculadas a uma mobilidade.',
 'outgoing_details': 'Informações complementares do Outgoing, incluindo previsão de retorno.',
 'applications': 'Candidaturas recebidas, estado de análise e eventual vínculo com a mobilidade criada.',
 'agreements': 'Acordos institucionais: vínculo com a universidade, informações processuais e vigência.',
 'agreement_actions': 'Etapas e ações de acompanhamento vinculadas a um acordo.',
 'integration_sources': 'Catálogo das fontes utilizadas nas integrações.',
 'agreement_source_records': 'Registros de origem dos acordos, para rastreabilidade da integração.',
 'user_accounts': 'Contas, perfil administrativo e permissões de acesso. Os valores dos campos não são exibidos neste inventário.',
 'dari_mobility_periods_v53_29': 'Evidências de vínculo entre mobilidade e semestre, com identificação da fonte.',
 'dari_local_edits_v53_30': 'Histórico de alterações locais: tipo de registro, identificador, usuário, data e versões anterior e posterior.',
}

def database_reference(db):
    inspector = inspect(db.connection())
    tables = []
    for name in sorted(inspector.get_table_names(), key=str.casefold):
        pk = set(inspector.get_pk_constraint(name).get('constrained_columns') or [])
        foreign = {}
        for fk in inspector.get_foreign_keys(name):
            for column, target in zip(fk.get('constrained_columns') or [], fk.get('referred_columns') or []):
                foreign[column] = f"{fk['referred_table']}.{target}"
        columns = [{'name': c['name'], 'type': str(c['type']), 'nullable': c.get('nullable'), 'primary': c['name'] in pk, 'reference': foreign.get(c['name'])} for c in inspector.get_columns(name)]
        tables.append({'name': name, 'description': DESCRIPTIONS.get(name, 'Tabela auxiliar. Consulte o código da integração ou migração correspondente para a regra de uso.'), 'columns': columns})
    return {'tables': tables, 'count': len(tables)}
