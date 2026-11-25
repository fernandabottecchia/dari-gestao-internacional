"""Production ASGI entrypoint; one worker prevents duplicate Google pollers."""
import os
import uvicorn
from app.database import engine
from app.hosting import validate_database

def main():
    validate_database(engine)
    uvicorn.run('app.main:app',host=os.getenv('DARI_BIND_HOST','0.0.0.0'),
        port=int(os.getenv('DARI_PORT','8000')),workers=1,proxy_headers=True,
        forwarded_allow_ips=os.getenv('DARI_FORWARDED_ALLOW_IPS','127.0.0.1'),
        server_header=False,log_level='info',timeout_keep_alive=5)
if __name__=='__main__':main()
