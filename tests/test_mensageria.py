"""Consumidor, relay e topologia com um canal falso; a prova contra um RabbitMQ real só roda se houver um."""
import json
import os as sistema
import socket
import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from pika.exceptions import UnroutableError
from sqlalchemy import select

from app.adapters.inbound.mensageria import tratar_mensagem
from app.adapters.outbound.mensageria import FILA_EVENTOS, declarar_topologia, publicar_pendentes
from app.domain.saga import PrazosSaga
from app.infrastructure.orm_mapping import outbox
from app.infrastructure.rastreamento import cabecalhos_de_rastreamento, consumo_rastreado
from tests.test_saga_fluxo import evento, os_aberta, pecas_reservadas  # noqa: F401

PRAZOS = PrazosSaga()


class CanalFalso:
    def __init__(self, recusar_rotas=()):
        self.publicadas, self.acks, self.nacks, self.chamadas = [], [], [], []
        self._recusar = recusar_rotas

    def basic_publish(self, exchange, rota, corpo, propriedades, mandatory):
        if rota in self._recusar:
            raise UnroutableError([])
        self.publicadas.append((exchange, rota, json.loads(corpo), propriedades))

    def basic_ack(self, tag):
        self.acks.append(tag)

    def basic_nack(self, tag, requeue):
        self.nacks.append((tag, requeue))

    def __getattr__(self, nome):
        return lambda *args, **kwargs: self.chamadas.append((nome, args, kwargs))


@pytest.fixture
def sessoes(db):
    @contextmanager
    def fabrica():
        yield db
    return fabrica


def entregar(canal, corpo, sessoes, headers=None):
    tratar_mensagem(canal, SimpleNamespace(delivery_tag=7), SimpleNamespace(headers=headers), corpo, sessoes, PRAZOS)


def test_evento_valido_e_confirmado_depois_do_processamento(os_aberta, sessoes):
    canal = CanalFalso()
    entregar(canal, json.dumps(evento(os_aberta, "PecasReservadas", pecas_reservadas(os_aberta))), sessoes,
             headers={"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"})
    assert canal.acks == [7] and canal.nacks == []


def test_mensagem_ilegivel_vai_direto_para_a_dlq(sessoes):
    canal = CanalFalso()
    entregar(canal, b"{nao e json", sessoes)
    entregar(canal, json.dumps({"tipo": "X"}), sessoes)
    assert canal.nacks == [(7, False), (7, False)]


def test_evento_de_saga_inexistente_vai_direto_para_a_dlq(os_aberta, sessoes):
    canal = CanalFalso()
    envelope = evento(os_aberta, "PecasReservadas", {})
    envelope["saga_id"] = str(uuid.uuid4())
    entregar(canal, json.dumps(envelope), sessoes)
    assert canal.nacks == [(7, False)]


def test_falha_inesperada_devolve_a_mensagem_para_a_fila(os_aberta, sessoes):
    canal = CanalFalso()
    entregar(canal, json.dumps(evento(os_aberta, "PecasReservadas", {"itens": "quebrado"})), sessoes)
    assert canal.nacks == [(7, True)] and canal.acks == []


def test_relay_publica_o_outbox_com_as_propriedades_do_contrato(os_aberta, sessoes, db):
    canal = CanalFalso()
    assert publicar_pendentes(canal, sessoes) == 1
    exchange, rota, corpo, propriedades = canal.publicadas[0]
    assert (exchange, rota, corpo["tipo"]) == ("oficina.comandos", "execucao.reservar-pecas", "ReservarPecas")
    assert propriedades.message_id == corpo["message_id"] and propriedades.type == "ReservarPecas"
    assert propriedades.correlation_id == os_aberta["saga"]["id"] and propriedades.delivery_mode == 2
    assert db.execute(select(outbox)).one().publicado_em is not None
    assert publicar_pendentes(canal, sessoes) == 0


def test_relay_para_quando_o_broker_recusa_e_tenta_de_novo_depois(os_aberta, sessoes, db):
    assert publicar_pendentes(CanalFalso(recusar_rotas={"execucao.reservar-pecas"}), sessoes) == 0
    assert db.execute(select(outbox)).one().publicado_em is None
    assert publicar_pendentes(CanalFalso(), sessoes) == 1


def test_topologia_declara_fila_quorum_com_limite_e_dlq():
    canal = CanalFalso()
    declarar_topologia(canal)
    filas = {args[0]: kwargs["arguments"] for nome, args, kwargs in canal.chamadas if nome == "queue_declare"}
    assert filas[FILA_EVENTOS] == {"x-queue-type": "quorum", "x-delivery-limit": 5,
                                   "x-dead-letter-exchange": "oficina.dlx", "x-dead-letter-routing-key": FILA_EVENTOS}
    ligacoes = {(args[0], args[1], args[2]) for nome, args, _ in canal.chamadas if nome == "queue_bind"}
    assert ligacoes == {(FILA_EVENTOS, "oficina.eventos", "execucao.#"), (FILA_EVENTOS, "oficina.eventos", "billing.#"),
                        (f"{FILA_EVENTOS}.dlq", "oficina.dlx", FILA_EVENTOS)}


def test_rastreamento_sem_agente_nao_interfere():
    assert cabecalhos_de_rastreamento() == {}
    with consumo_rastreado("PecasReservadas", None):
        pass


RABBITMQ_URL = sistema.environ.get("RABBITMQ_URL_TESTE", "amqp://oficina:oficina@localhost:5672/%2F")


def rabbitmq_disponivel():
    try:
        socket.create_connection(("localhost", 5672), timeout=0.5).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not rabbitmq_disponivel(), reason="RabbitMQ local não está no ar (make local-up na plataforma)")
def test_trabalhador_contra_rabbitmq_real(os_aberta, sessoes, db):
    """Publica o ReservarPecas, responde como a Execução e confere que a Saga avançou."""
    import pika
    from app.infrastructure.trabalhador import TrabalhadorMensageria

    trabalhador = TrabalhadorMensageria(RABBITMQ_URL, sessoes, PRAZOS, intervalo_prazos=3600)
    conexao = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
    canal = conexao.channel()
    canal.exchange_declare("oficina.comandos", "topic", durable=True)
    fila_teste = canal.queue_declare("", exclusive=True).method.queue
    canal.queue_bind(fila_teste, "oficina.comandos", "execucao.#")
    trabalhador.start()
    try:
        comando = None
        for _ in range(50):
            metodo, _, corpo = canal.basic_get(fila_teste, auto_ack=True)
            if metodo:
                comando = json.loads(corpo)
                break
            time.sleep(0.1)
        assert comando and comando["tipo"] == "ReservarPecas"

        resposta = evento(os_aberta, "PecasReservadas", pecas_reservadas(os_aberta))
        canal.basic_publish("oficina.eventos", "execucao.pecas-reservadas", json.dumps(resposta).encode())
        tipos = []
        for _ in range(50):
            time.sleep(0.1)
            tipos = [m.corpo["tipo"] for m in db.execute(select(outbox)).all()]
            if "GerarOrcamento" in tipos:
                break
        assert tipos == ["ReservarPecas", "GerarOrcamento"]
    finally:
        trabalhador.parar()
        canal.queue_delete(FILA_EVENTOS)
        canal.queue_delete(f"{FILA_EVENTOS}.dlq")
        conexao.close()
