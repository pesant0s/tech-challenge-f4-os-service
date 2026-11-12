"""Consumo dos eventos de Execução e Billing: ack só depois do commit (ADR-021)."""
import json
import logging
import uuid

from app.adapters.outbound.persistence.cliente_repository import ClienteRepositoryAdapter
from app.adapters.outbound.persistence.os_repository import OSRepositoryAdapter
from app.adapters.outbound.persistence.outbox_repository import InboxRepositoryAdapter, OutboxRepositoryAdapter
from app.adapters.outbound.persistence.saga_repository import SagaRepositoryAdapter
from app.application.use_cases.saga_os import ProcessarEventoSagaUseCase
from app.domain.exceptions import NotFoundException
from app.infrastructure.logging_config import definir_correlation_id
from app.infrastructure.rastreamento import consumo_rastreado

logger = logging.getLogger("oficina.mensageria")


def tratar_mensagem(canal, metodo, propriedades, corpo, sessoes, prazos) -> None:
    """Mensagem ilegível ou de Saga inexistente vai direto para a DLQ; erro inesperado volta para a fila."""
    try:
        envelope = json.loads(corpo)
        tipo, saga_id = envelope["tipo"], str(uuid.UUID(envelope["saga_id"]))
        uuid.UUID(envelope["message_id"])
    except (ValueError, KeyError, TypeError, AttributeError):
        logger.error("Mensagem ilegível descartada para a DLQ", extra={"evento": "mensagem_invalida"})
        canal.basic_nack(metodo.delivery_tag, requeue=False)
        return
    definir_correlation_id(saga_id)
    with consumo_rastreado(tipo, propriedades.headers), sessoes() as db:
        try:
            ProcessarEventoSagaUseCase(
                SagaRepositoryAdapter(db), OSRepositoryAdapter(db), ClienteRepositoryAdapter(db),
                OutboxRepositoryAdapter(db), InboxRepositoryAdapter(db), prazos,
            ).executar(envelope)
        except NotFoundException as exc:
            db.rollback()
            logger.error("%s: %s descartada para a DLQ", exc.detail, tipo, extra={"evento": "mensagem_sem_saga", "tipo": tipo})
            canal.basic_nack(metodo.delivery_tag, requeue=False)
            return
        except Exception:
            db.rollback()
            logger.exception("Falha ao processar %s; a mensagem volta para a fila", tipo, extra={"evento": "falha_no_consumo", "tipo": tipo})
            canal.basic_nack(metodo.delivery_tag, requeue=True)
            return
    canal.basic_ack(metodo.delivery_tag)
