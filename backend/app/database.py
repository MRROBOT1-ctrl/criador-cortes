"""
Setup do SQLAlchemy (síncrono — tanto a API quanto os workers Celery usam a
mesma engine; para o volume do MVP isso é simples e suficiente).
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)

if settings.database_url.startswith("sqlite"):
    # WAL permite leitores e escritores concorrentes sem se bloquearem (o
    # modo padrão do SQLite serializa TUDO); busy_timeout faz uma escrita
    # que colide esperar um pouco em vez de estourar "database is locked"
    # na hora. Necessário aqui porque o processo da API e o worker Celery
    # (containers separados) batem no mesmo arquivo .db ao mesmo tempo — o
    # worker commita a cada fase/clipe enquanto o WebSocket de status abre
    # uma sessão nova a cada segundo pra pollar o mesmo banco.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def get_db():
    """Dependency do FastAPI: abre uma sessão por request e garante o fechamento."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Cria as tabelas se não existirem. Suficiente para o estágio de MVP
    (sem migrations formais ainda — considerar Alembic quando o schema estabilizar)."""
    from app import models  # noqa: F401 — garante que os modelos sejam registrados

    Base.metadata.create_all(bind=engine)
