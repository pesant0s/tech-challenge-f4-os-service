"""Casos de uso da Saga da abertura da OS (ADR-018); a ordem das travas é sempre Saga e depois OS."""
import logging
from datetime import datetime, timezone

from app.application.eventos_os import registrar_mudanca_de_status
from app.application.mensagens import EXCHANGE_COMANDOS, montar_envelope
from app.domain.exceptions import NotFoundException
from app.domain.saga import PrazosSaga, SagaAberturaOS

logger = logging.getLogger("oficina.saga")


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def enviar_comandos(outbox, saga: SagaAberturaOS, comandos, agora: datetime) -> None:
    for comando in comandos:
        outbox.adicionar(montar_envelope(comando.tipo, saga.id, saga.os_id, comando.payload, agora),
                         EXCHANGE_COMANDOS, comando.rota)


def registrar_passo(saga: SagaAberturaOS, gatilho: str, comandos) -> None:
    logger.info("Saga %s · %s → %s/%s", saga.id, gatilho, saga.passo.value, saga.estado.value, extra={
        "evento": "saga",
        "saga_id": str(saga.id),
        "os_id": str(saga.os_id),
        "gatilho": gatilho,
        "passo": saga.passo.value,
        "estado": saga.estado.value,
        "motivo": saga.motivo,
        "comandos": [c.tipo for c in comandos] or None,
    })


def registrar_status_novos(os, quantos_antes: int) -> None:
    for indice in range(quantos_antes, len(os.historico)):
        registrar_mudanca_de_status(os, indice)


class ProcessarEventoSagaUseCase:
    """Aplica um evento de Execução ou Billing à Saga, uma única vez por message_id."""

    def __init__(self, saga_repo, os_repo, cliente_repo, outbox_repo, inbox_repo, prazos: PrazosSaga):
        self._sagas = saga_repo
        self._os = os_repo
        self._clientes = cliente_repo
        self._outbox = outbox_repo
        self._inbox = inbox_repo
        self._prazos = prazos

    def executar(self, envelope: dict, agora: datetime | None = None) -> bool:
        """False quando a mensagem já tinha sido processada."""
        agora = agora or agora_utc()
        tipo = envelope["tipo"]
        if not self._inbox.registrar(envelope["message_id"], tipo):
            logger.info("Mensagem %s repetida, ignorada", envelope["message_id"], extra={"evento": "mensagem_repetida", "tipo": tipo})
            return False
        saga = self._sagas.buscar_para_escrita(envelope["saga_id"])
        os = self._os.buscar_para_escrita(saga.os_id)
        quantos_antes = len(os.historico)
        comandos = saga.ao_receber(tipo, envelope["payload"], os, self._cliente(os, tipo), agora, self._prazos)
        if comandos is None:
            logger.warning("Evento %s não cabe na Saga %s (%s, %s), descartado", tipo, saga.id, saga.passo.value, saga.estado.value,
                           extra={"evento": "saga_evento_descartado", "saga_id": str(saga.id), "tipo": tipo})
            comandos = []
        enviar_comandos(self._outbox, saga, comandos, agora)
        self._os.commit()
        registrar_status_novos(os, quantos_antes)
        registrar_passo(saga, tipo, comandos)
        return True

    def _cliente(self, os, tipo):
        # Só o orçamento precisa dos dados do cliente; o CPF/CNPJ não sai do OS Service.
        if tipo != "PecasReservadas":
            return None
        cliente = self._clientes.buscar_por_id(os.cliente_id)
        return {"id": str(cliente.id), "nome": cliente.nome, "email": cliente.email, "telefone": cliente.telefone}


class VerificarPrazosSagaUseCase:
    """Varre as Sagas com prazo esgotado e aplica a regra de cada passo."""

    def __init__(self, saga_repo, os_repo, outbox_repo, prazos: PrazosSaga):
        self._sagas = saga_repo
        self._os = os_repo
        self._outbox = outbox_repo
        self._prazos = prazos

    def executar(self, agora: datetime | None = None, limite: int = 50) -> int:
        agora = agora or agora_utc()
        vencidas = self._sagas.vencidas_para_escrita(agora, limite)
        alteradas = []
        for saga in vencidas:
            os = self._os.buscar_para_escrita(saga.os_id)
            comandos = saga.ao_expirar(os, agora, self._prazos)
            enviar_comandos(self._outbox, saga, comandos, agora)
            alteradas.append((saga, os, len(os.historico), comandos))
        self._os.commit()
        for saga, os, quantos_antes, comandos in alteradas:
            registrar_status_novos(os, quantos_antes)
            registrar_passo(saga, "PrazoEsgotado", comandos)
        return len(vencidas)


class CancelarOSUseCase:
    """Cancelamento pela oficina antes do pagamento: dispara as compensações da Saga."""

    def __init__(self, saga_repo, os_repo, outbox_repo, prazos: PrazosSaga):
        self._sagas = saga_repo
        self._os = os_repo
        self._outbox = outbox_repo
        self._prazos = prazos

    def executar(self, os_id, agora: datetime | None = None):
        agora = agora or agora_utc()
        saga = self._sagas.buscar_por_os(os_id)
        if not saga:
            raise NotFoundException("Ordem de Serviço não encontrada")
        saga = self._sagas.buscar_para_escrita(saga.id)
        os = self._os.buscar_para_escrita(os_id)
        quantos_antes = len(os.historico)
        comandos = saga.cancelar_pela_oficina(os, agora, self._prazos)
        enviar_comandos(self._outbox, saga, comandos, agora)
        self._os.commit()
        self._os.refresh(os)
        registrar_status_novos(os, quantos_antes)
        registrar_passo(saga, "CanceladaPelaOficina", comandos)
        return os
