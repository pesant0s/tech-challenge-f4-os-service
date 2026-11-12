"""Thread de mensageria do processo: consome eventos, publica o outbox e varre prazos, com reconexão."""
import logging
import threading
import time
from functools import partial

import pika

from app.adapters.inbound.mensageria import tratar_mensagem
from app.adapters.outbound.mensageria import FILA_EVENTOS, declarar_topologia, publicar_pendentes
from app.adapters.outbound.persistence.os_repository import OSRepositoryAdapter
from app.adapters.outbound.persistence.outbox_repository import OutboxRepositoryAdapter
from app.adapters.outbound.persistence.saga_repository import SagaRepositoryAdapter
from app.application.use_cases.saga_os import VerificarPrazosSagaUseCase

logger = logging.getLogger("oficina.mensageria")


def varrer_prazos(sessoes, prazos) -> int:
    with sessoes() as db:
        return VerificarPrazosSagaUseCase(
            SagaRepositoryAdapter(db), OSRepositoryAdapter(db), OutboxRepositoryAdapter(db), prazos,
        ).executar()


class TrabalhadorMensageria(threading.Thread):
    def __init__(self, url: str, sessoes, prazos, intervalo_prazos: float = 30):
        super().__init__(name="mensageria", daemon=True)
        self._url = url
        self._sessoes = sessoes
        self._prazos = prazos
        self._intervalo_prazos = intervalo_prazos
        self._parar = threading.Event()

    def parar(self) -> None:
        self._parar.set()
        self.join(timeout=5)

    def run(self) -> None:
        espera = 1
        while not self._parar.is_set():
            try:
                self._trabalhar()
                espera = 1
            except Exception as exc:  # broker ou banco fora do ar: a thread não pode morrer
                logger.warning("Mensageria interrompida (%s); nova tentativa em %ss", exc, espera,
                               extra={"evento": "mensageria_reconectando"})
                self._parar.wait(espera)
                espera = min(espera * 2, 30)

    def _trabalhar(self) -> None:
        conexao = pika.BlockingConnection(pika.URLParameters(self._url))
        try:
            canal = conexao.channel()
            declarar_topologia(canal)
            canal.confirm_delivery()
            canal.basic_qos(prefetch_count=10)
            canal.basic_consume(FILA_EVENTOS, partial(tratar_mensagem, sessoes=self._sessoes, prazos=self._prazos))
            logger.info("Mensageria conectada", extra={"evento": "mensageria_conectada"})
            proxima_varredura = 0.0
            while not self._parar.is_set():
                conexao.process_data_events(time_limit=1)
                publicar_pendentes(canal, self._sessoes)
                if time.monotonic() >= proxima_varredura:
                    varrer_prazos(self._sessoes, self._prazos)
                    proxima_varredura = time.monotonic() + self._intervalo_prazos
        finally:
            if conexao.is_open:
                conexao.close()
