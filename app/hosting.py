"""Configuration and security for the hosted, single-database edition."""
import base64
import hashlib
import hmac
import json
import os
import time
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from urllib.parse import urlsplit
from fastapi.responses import JSONResponse
from sqlalchemy import text

@dataclass(frozen=True)
class Settings:
    production: bool
    secret: str
    public_url: str


def configuration():
    mode=os.getenv('DARI_ENV','production')
    if mode not in ('production','test'):
        raise RuntimeError('DARI_ENV deve ser production (ou test somente nos testes).')
    production=mode=='production'
    secret=os.getenv('DARI_SECRET_KEY','')
    url=os.getenv('DARI_PUBLIC_URL','').rstrip('/')
    parsed=urlsplit(url)
    if len(secret)<48 or secret.startswith('TROCAR_'):
        raise RuntimeError('Defina DARI_SECRET_KEY com pelo menos 48 caracteres aleatórios.')
    if not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise RuntimeError('Defina DARI_PUBLIC_URL somente com esquema e domínio, sem caminho.')
    if production and parsed.scheme!='https':
        raise RuntimeError('A hospedagem exige DARI_PUBLIC_URL com https://.')
    if not production and parsed.scheme not in ('http','https'):
        raise RuntimeError('URL de teste inválida.')
    return Settings(production,secret,url)

settings=configuration()
TTL=8*60*60

def password_ok(value):
    return len(value)>=12 and value.casefold() not in ('password1234','123456789012','dari2026!')

def hash_password(value):
    import secrets
    salt=secrets.token_hex(16); iterations=260000
    digest=hashlib.pbkdf2_hmac('sha256',value.encode(),salt.encode(),iterations).hex()
    return f'pbkdf2_sha256${iterations}${salt}${digest}'

def verify_password(value,stored):
    try:
        algo,n,salt,expected=stored.split('$')
        if algo!='pbkdf2_sha256' or not 100000<=int(n)<=2000000:return False
        got=hashlib.pbkdf2_hmac('sha256',value.encode(),salt.encode(),int(n)).hex()
        return hmac.compare_digest(got,expected)
    except (ValueError,TypeError):return False

def issue_session(user,password_hash):
    data={'user':user,'issued':int(time.time()),'version':hashlib.sha256(password_hash.encode()).hexdigest()}
    payload=base64.urlsafe_b64encode(json.dumps(data,separators=(',',':')).encode()).decode().rstrip('=')
    return payload+'.'+hmac.new(settings.secret.encode(),payload.encode(),hashlib.sha256).hexdigest()

def read_session(token,password_hash=None):
    try:
        payload,signature=token.rsplit('.',1)
        expected=hmac.new(settings.secret.encode(),payload.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):return {}
        data=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        age=time.time()-data['issued']
        if not 0<=age<TTL:return {}
        if password_hash is not None and not hmac.compare_digest(data['version'],hashlib.sha256(password_hash.encode()).hexdigest()):return {}
        return data if isinstance(data.get('user'),str) else {}
    except (ValueError,KeyError,TypeError):return {}

_attempts=OrderedDict();_lock=threading.Lock()
def login_allowed(ip):
    now=time.monotonic()
    with _lock:
        q=_attempts.setdefault(ip,deque())
        while q and q[0]<now-300:q.popleft()
        if len(q)>=20:return False
        q.append(now);_attempts.move_to_end(ip)
        while len(_attempts)>10000:_attempts.popitem(last=False)
    return True

async def security_middleware(request,call_next):
    # All unsafe browser requests need an exact configured same-origin match.
    # Existing per-form CSRF tokens continue to be checked by their handlers.
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if not origin:
            ref=urlsplit(request.headers.get('referer',''))
            origin=f'{ref.scheme}://{ref.netloc}' if ref.netloc else ''
        if origin!=settings.public_url:
            return JSONResponse({'detail':'Origem da requisição inválida. Reabra o formulário no endereço oficial.'},403)
    if request.url.path=='/login' and request.method=='POST':
        if not login_allowed(request.client.host if request.client else 'unknown'):
            return JSONResponse({'detail':'Muitas tentativas. Aguarde cinco minutos.'},429,headers={'Retry-After':'300'})
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Cache-Control']='no-store'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    return response

def validate_database(engine):
    if settings.production and engine.dialect.name!='postgresql':
        raise RuntimeError('A edição hospedada exige PostgreSQL; não há fallback para SQLite.')
    with engine.connect() as conn:
        if conn.scalar(text("SELECT release FROM dari_hosting_migration WHERE id=1"))!='53.49.0-rc1':
            raise RuntimeError('Execute a migração validada antes de iniciar a aplicação.')
        if not conn.scalar(text('SELECT COUNT(*) FROM user_accounts WHERE is_active=TRUE AND is_admin=TRUE')):
            raise RuntimeError('Defina uma conta administradora ativa com tools.accounts.')
        for value in conn.scalars(text('SELECT password_hash FROM user_accounts WHERE is_active=TRUE')):
            if verify_password('DARI2026!',value):
                raise RuntimeError('Ainda há conta ativa com a senha antiga padrão. Redefina ou desative com tools.accounts.')
