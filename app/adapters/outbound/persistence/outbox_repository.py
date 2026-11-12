"""Outbox e inbox da mensageria (ADR-021): gravados na mesma transação da mudança de estado."""
import uuid

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.orm_mapping import mensagens_processadas, outbox
from app.infrastructure.rastreamento import cabecalhos_de_rastreamento


class OutboxRepositoryAdapter:
    def __init__(self, db: Session):
        self._db = db

    def adicionar(self, envelope: dict, exchange: str, rota: str) -> None:
        """Guarda também o contexto do trace atual: o relay publica depois, fora desta transação."""
        self._db.execute(insert(outbox).values(
            id=uuid.UUID(envelope["message_id"]), exchange=exchange, rota=rota,
            tipo=envelope["tipo"], corpo=envelope, cabecalhos=cabecalhos_de_rastreamento(),
        ))

    def pendentes_para_publicar(self, limite: int) -> list:
        return self._db.execute(
            select(outbox).where(outbox.c.publicado_em.is_(None))
            .order_by(outbox.c.criado_em).limit(limite).with_for_update(skip_locked=True)
        ).all()

    def marcar_publicada(self, mensagem_id, agora) -> None:
        self._db.execute(update(outbox).where(outbox.c.id == mensagem_id).values(publicado_em=agora))


class InboxRepositoryAdapter:
    def __init__(self, db: Session):
        self._db = db

    def registrar(self, message_id: str, tipo: str) -> bool:
        """Grava o message_id; False quando ele já tinha sido processado."""
        try:
            with self._db.begin_nested():
                self._db.execute(insert(mensagens_processadas).values(message_id=uuid.UUID(message_id), tipo=tipo))
        except IntegrityError:
            return False
        return True
