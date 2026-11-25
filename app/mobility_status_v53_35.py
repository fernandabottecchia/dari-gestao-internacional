from .mobility_periods_v53_29 import key
from .mobility_workflow_v53_16 import state_of,change_state
CHOICES={'active','completed','cancelled','unclassified','custom'}
def selection(db,m,active):
    if not m:return 'unclassified'
    if state_of(db,m.id)=='completed':return 'completed'
    if any(s in key(m.status) for s in ('desist','cancel','rejeit')):return 'cancelled'
    if active:return 'active'
    if getattr(m,'_status_color34',''):return 'custom'
    if m.status and key(m.status) not in ('ativo','sem classificacao','processo concluido'):return 'custom'
    return 'active' if active else 'unclassified'
def validate(choice,status):
    if choice not in CHOICES:raise ValueError('Selecione um status da lista.')
    if choice=='custom' and not status.strip():raise ValueError('Informe o texto do status personalizado.')
def apply(db,m,choice,previous,status,checked,was_active,user):
    m.status=status.strip() if choice=='custom' else {'active':'Ativo','completed':'Processo concluído','cancelled':'Desistente / cancelado','unclassified':None}[choice]
    target={'active':'active','completed':'completed'}.get(choice,'unclassify')
    change_state(db,m.id,target,user,commit=False,force=True)
