"""Explicit password reset/deactivation after migration. No default passwords."""
import argparse,getpass
from sqlalchemy import select
from app.database import SessionLocal
from app.models import UserAccount
from app.hosting import hash_password,password_ok

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['reset','disable','list'])
    p.add_argument('--username');p.add_argument('--create-admin',action='store_true')
    a=p.parse_args()
    with SessionLocal() as db:
        if a.action=='list':
            for u in db.scalars(select(UserAccount).order_by(UserAccount.username)):
                print(u.username,'administrador' if u.is_admin else 'equipe','ativo' if u.is_active else 'inativo')
            return
        if not a.username:p.error('--username é obrigatório')
        u=db.scalar(select(UserAccount).where(UserAccount.username==a.username))
        if a.action=='disable':
            if not u:raise SystemExit('Conta não encontrada.')
            u.is_active=False
        else:
            if not u:
                if not a.create_admin:raise SystemExit('Conta não encontrada; --create-admin cria uma nova conta explicitamente.')
                u=UserAccount(username=a.username,display_name=a.username,is_admin=True);db.add(u)
            pw=getpass.getpass('Nova senha (não aparece): ')
            if not password_ok(pw):raise SystemExit('Use uma senha exclusiva com pelo menos 12 caracteres.')
            if getpass.getpass('Repita a senha: ')!=pw:raise SystemExit('Senhas diferentes.')
            u.password_hash=hash_password(pw);u.is_active=True
        db.commit()
    print('Conta atualizada.')
if __name__=='__main__':main()
