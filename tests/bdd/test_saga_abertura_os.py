"""Passos da feature: relay e consumidor reais ligados a um broker em memória, no lugar do RabbitMQ."""
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from app.adapters.inbound.mensageria import tratar_mensagem
from app.adapters.outbound.mensageria import publicar_pendentes
from app.domain.saga import PrazosSaga

scenarios("saga_abertura_os.feature")

EVENTOS_DA_EXECUCAO = {"PecasReservadas", "ReservaRecusada", "ReservaLiberada", "OSEnfileirada",
                       "EnfileiramentoRecusado", "DiagnosticoIniciado", "ExecucaoIniciada", "ExecucaoFinalizada"}
CAMINHO_FELIZ = ["PecasReservadas", "OrcamentoGerado", "OrcamentoAprovado", "CobrancaCriada", "PagamentoConfirmado"]
CONFIRMACAO_DA_COMPENSACAO = {"LiberarReserva": "ReservaLiberada", "CancelarOrcamento": "OrcamentoCancelado",
                              "Reembolsar": "PagamentoReembolsado"}


class BrokerEmMemoria:
    """Faz o papel do RabbitMQ: guarda o que o relay publica e entrega eventos pelo consumidor real."""

    def __init__(self, sessoes):
        self._sessoes = sessoes
        self.publicadas = []
        self._lidas = 0
        self._ultima = None

    def basic_publish(self, exchange, rota, corpo, propriedades, mandatory):
        self.publicadas.append(json.loads(corpo))

    def basic_ack(self, tag):
        pass

    def basic_nack(self, tag, requeue):
        raise AssertionError("o OS Service recusou um evento válido")

    def comandos_novos(self) -> list[str]:
        publicar_pendentes(self, self._sessoes)
        novos, self._lidas = self.publicadas[self._lidas:], len(self.publicadas)
        return [m["tipo"] for m in novos]

    def entregar(self, os_aberta, tipo, payload):
        self._ultima = json.dumps({
            "message_id": str(uuid.uuid4()), "tipo": tipo, "versao": 1,
            "saga_id": os_aberta["saga"]["id"], "os_id": os_aberta["id"],
            "ocorrido_em": datetime.now(timezone.utc).isoformat(),
            "origem": "execucao-service" if tipo in EVENTOS_DA_EXECUCAO else "billing-service",
            "payload": payload,
        })
        self.reentregar()

    def reentregar(self):
        tratar_mensagem(self, SimpleNamespace(delivery_tag=1), SimpleNamespace(headers={}),
                        self._ultima, self._sessoes, PrazosSaga())


def payload_de(tipo, os_aberta, valor_total="150.00"):
    agora = datetime.now(timezone.utc)
    precificados = [{"tipo": i["tipo"], "item_id": i["item_id"], "descricao": "Item", "quantidade": i["quantidade"],
                     "preco_unitario": "50.00", "subtotal": f"{50 * i['quantidade']}.00"} for i in os_aberta["itens"]]
    return {
        "PecasReservadas": {"itens": precificados, "valor_total": valor_total},
        "OrcamentoGerado": {"orcamento_id": "orc-1", "valor_total": valor_total,
                            "expira_em": (agora + timedelta(hours=1)).isoformat()},
        "OrcamentoAprovado": {"orcamento_id": "orc-1", "aprovado_por": "CLIENTE"},
        "CobrancaCriada": {"pagamento_id": "pag-1", "provedor": "SIMULADO", "valor": valor_total,
                           "qr_data": "00020101SIMULADO", "expira_em": (agora + timedelta(minutes=15)).isoformat()},
        "PagamentoConfirmado": {"pagamento_id": "pag-1", "valor": valor_total, "pago_em": agora.isoformat()},
        "OSEnfileirada": {"posicao": 1},
        "ReservaRecusada": {"motivo": "ESTOQUE_INSUFICIENTE", "itens": []},
        "OrcamentoRejeitado": {"orcamento_id": "orc-1", "motivo": "ORCAMENTO_REJEITADO"},
        "PagamentoRecusado": {"pagamento_id": "pag-1", "motivo": "PAGAMENTO_RECUSADO"},
        "EnfileiramentoRecusado": {"motivo": "RESERVA_INEXISTENTE"},
        "ReservaLiberada": {},
        "OrcamentoCancelado": {"orcamento_id": "orc-1"},
        "PagamentoReembolsado": {"pagamento_id": "pag-1", "valor": valor_total},
    }.get(tipo, {"observacao": None})


@pytest.fixture
def broker(db):
    @contextmanager
    def sessoes():
        yield db
    return BrokerEmMemoria(sessoes)


@pytest.fixture
def contexto():
    return {}


@given("um cliente com um veículo cadastrado")
def cliente_com_veiculo(client, auth_headers, contexto):
    cliente = client.post("/cadastro/clientes", headers=auth_headers, json={
        "nome": "Ana Souza", "cpf_cnpj": "52998224725", "telefone": "11999990000", "email": "ana@exemplo.com",
    }).json()
    contexto["veiculo"] = client.post("/cadastro/veiculos", headers=auth_headers, json={
        "placa": "ABC1D23", "marca": "Fiat", "modelo": "Uno", "ano": "2020", "cliente_id": cliente["id"],
    }).json()
    contexto["cliente"] = cliente


@given("o atendente abre uma OS com 1 serviço e 2 unidades de uma peça")
def abrir_os(client, auth_headers, contexto):
    resposta = client.post("/atendimento/os", headers=auth_headers, json={
        "cliente_id": contexto["cliente"]["id"], "veiculo_id": contexto["veiculo"]["id"],
        "servicos": [{"servico_id": str(uuid.uuid4()), "quantidade": 1}],
        "pecas": [{"peca_id": str(uuid.uuid4()), "quantidade": 2}],
    })
    assert resposta.status_code == 201, resposta.text
    contexto["os"] = resposta.json()


@given(parsers.re(r'^que a Saga chegou até "(?P<ultimo_sucesso>[^"]+)"$'))
def saga_ate(broker, contexto, ultimo_sucesso):
    broker.comandos_novos()
    if ultimo_sucesso == "ReservarPecas":
        return
    for tipo in CAMINHO_FELIZ[:CAMINHO_FELIZ.index(ultimo_sucesso) + 1]:
        broker.entregar(contexto["os"], tipo, payload_de(tipo, contexto["os"]))
    broker.comandos_novos()


@when(parsers.re(r'^a Execução responde "(?P<tipo>[^"]+)" com valor total de "(?P<valor>[^"]+)"$'))
def execucao_responde_com_valor(broker, contexto, tipo, valor):
    broker.entregar(contexto["os"], tipo, payload_de(tipo, contexto["os"], valor))


@when(parsers.re(r'^a Execução responde "(?P<tipo>[^"]+)"$'))
@when(parsers.re(r'^o Billing responde "(?P<tipo>[^"]+)"$'))
@when(parsers.re(r'^chega a falha "(?P<tipo>[^"]+)"$'))
def participante_responde(broker, contexto, tipo):
    broker.entregar(contexto["os"], tipo, payload_de(tipo, contexto["os"]))


@when("o broker entrega de novo a última mensagem")
def reentregar(broker):
    broker.reentregar()


@when("o atendente entrega o veículo")
def entregar_veiculo(client, auth_headers, contexto):
    assert client.post(f"/atendimento/os/{contexto['os']['id']}/entregar", headers=auth_headers).status_code == 200


@then(parsers.re(r'^a Execução recebe o comando "(?P<tipo>[^"]+)"$'))
@then(parsers.re(r'^o Billing recebe o comando "(?P<tipo>[^"]+)"$'))
def participante_recebe(broker, tipo):
    assert tipo in broker.comandos_novos()


@then(parsers.re(r'^o Billing recebe o comando "(?P<tipo>[^"]+)" uma única vez$'))
def recebe_uma_vez(broker, tipo):
    broker.comandos_novos()
    assert [m["tipo"] for m in broker.publicadas].count(tipo) == 1


@then(parsers.re(r'^a OS fica "(?P<status>[^"]+)"$'))
def os_fica(client, auth_headers, contexto, status):
    assert client.get(f"/atendimento/os/{contexto['os']['id']}", headers=auth_headers).json()["status"] == status


@then(parsers.re(r'^a Saga termina "(?P<estado>[^"]+)"$'))
def saga_termina(client, auth_headers, contexto, estado):
    assert client.get(f"/atendimento/os/{contexto['os']['id']}", headers=auth_headers).json()["saga"]["estado"] == estado


@then(parsers.re(r'^as compensações saem nesta ordem: "(?P<compensacoes>[^"]+)"$'))
def compensacoes_em_ordem(broker, contexto, compensacoes):
    esperadas = [] if compensacoes == "nenhuma" else [c.strip() for c in compensacoes.split(",")]
    enviadas = []
    for _ in esperadas:
        [comando] = broker.comandos_novos()
        enviadas.append(comando)
        confirmacao = CONFIRMACAO_DA_COMPENSACAO[comando]
        broker.entregar(contexto["os"], confirmacao, payload_de(confirmacao, contexto["os"]))
    assert enviadas == esperadas
    assert broker.comandos_novos() == []


@then(parsers.re(r'^a OS termina "(?P<status>[^"]+)" com a Saga "(?P<estado>[^"]+)"$'))
def os_termina(client, auth_headers, contexto, status, estado):
    os_final = client.get(f"/atendimento/os/{contexto['os']['id']}", headers=auth_headers).json()
    assert (os_final["status"], os_final["saga"]["estado"]) == (status, estado)
