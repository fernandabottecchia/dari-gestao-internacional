from sqlalchemy import text
PALETTE={
 'amber':{'name':'Âmbar','foreground':'#854d0e','background':'#fef3c7','border':'#d97706'},
 'gray':{'name':'Cinza claro','foreground':'#475569','background':'#f1f5f9','border':'#cbd5e1'},
 'brown':{'name':'Marrom','foreground':'#663c26','background':'#f1e3d6','border':'#9a6240'},
}
def preferences(db):
    return {r['mobility_id']:r['color'] for r in db.execute(text('SELECT mobility_id,color FROM dari_mobility_presentation_v53_34')).mappings()}
def attach(db,rows):
    values=preferences(db)
    from .mobility_workflow_v53_16 import state_map
    from .mobility_periods_v53_29 import key
    states=state_map(db,[m.id for m in rows])
    for m in rows:
        custom=bool(values.get(m.id)) or bool(m.status and key(m.status) not in ('ativo','sem classificacao','processo concluido'))
        if states.get(m.id)=='completed' or any(s in key(m.status) for s in ('desist','cancel','rejeit')):custom=False
        m._status_color34='gray' if custom else ''
        m._status_palette34=PALETTE['gray'] if custom else None
def context(db,m):
    from .mobility_periods_v53_29 import activity
    if m:attach(db,[m])
    active=bool(m and m.id in activity(db,[m]))
    from .mobility_status_v53_35 import selection
    return {'status_choice35':selection(db,m,active),'status_palette34':PALETTE,'selected_color34':getattr(m,'_status_color34','') if m else '',
            'mobility_is_active34':active}
def save(db,mid,color):
    if color:color='gray'
    if color:
        db.execute(text('INSERT INTO dari_mobility_presentation_v53_34(mobility_id,color) VALUES (:id,:color) ON CONFLICT(mobility_id) DO UPDATE SET color=excluded.color'),{'id':mid,'color':color})
    else:db.execute(text('DELETE FROM dari_mobility_presentation_v53_34 WHERE mobility_id=:id'),{'id':mid})
