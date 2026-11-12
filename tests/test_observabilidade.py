"""Correlação de requisições, logs estruturados e healthchecks."""
import json
import logging
import uuid

from app.infrastructure.logging_config import (
    ConsoleFormatter,
    JsonFormatter,
    definir_correlation_id,
)


def _registro(mensagem: str = "teste", **extras) -> logging.LogRecord:
    registro = logging.LogRecord(
        name="oficina.teste", level=logging.INFO, pathname=__file__,
        lineno=1, msg=mensagem, args=(), exc_info=None,
    )
    for chave, valor in extras.items():
        setattr(registro, chave, valor)
    return registro


def test_log_json_tem_os_campos_obrigatorios():
    definir_correlation_id("abc-123")
    evento = json.loads(JsonFormatter().format(_registro("OS criada")))
    assert evento["level"] == "INFO"
    assert evento["logger"] == "oficina.teste"
    assert evento["message"] == "OS criada"
    assert evento["correlation_id"] == "abc-123"
    assert "timestamp" in evento


def test_log_json_carrega_campos_extras():
    definir_correlation_id("abc-123")
    evento = json.loads(JsonFormatter().format(_registro(os_id="os-1", duracao_ms=12.5)))
    assert evento["os_id"] == "os-1"
    assert evento["duracao_ms"] == 12.5


def test_log_json_liga_a_linha_ao_trace_do_new_relic(monkeypatch):
    vinculo = {"trace.id": "t-1", "span.id": "s-1", "entity.guid": "g-1"}
    monkeypatch.setattr("newrelic.agent.get_linking_metadata", lambda: vinculo)
    evento = json.loads(JsonFormatter().format(_registro()))
    assert {k: evento[k] for k in vinculo} == vinculo


def test_log_json_omite_campos_nulos():
    evento = json.loads(JsonFormatter().format(_registro(status_anterior=None, status_novo="ABERTA")))
    assert "status_anterior" not in evento
    assert evento["status_novo"] == "ABERTA"


def test_log_json_omite_correlation_id_fora_de_requisicao():
    definir_correlation_id("")
    evento = json.loads(JsonFormatter().format(_registro()))
    assert "correlation_id" not in evento


def test_log_json_serializa_valor_nao_serializavel():
    definir_correlation_id("")
    evento = json.loads(JsonFormatter().format(_registro(objeto=object())))
    assert isinstance(evento["objeto"], str)


def test_formato_console_e_legivel():
    definir_correlation_id("abcdef12-3456")
    linha = ConsoleFormatter().format(_registro("mensagem legível"))
    assert "mensagem legível" in linha
    assert "abcdef12" in linha
    assert not linha.startswith("{")


def test_resposta_devolve_x_request_id(client):
    resposta = client.get("/health")
    assert resposta.status_code == 200
    assert uuid.UUID(resposta.headers["x-request-id"])


def test_correlation_id_recebido_e_preservado(client):
    enviado = "id-vindo-do-api-gateway"
    resposta = client.get("/health", headers={"x-request-id": enviado})
    assert resposta.headers["x-request-id"] == enviado


def test_erro_de_negocio_devolve_correlation_id(client, auth_headers):
    resposta = client.get("/cadastro/clientes/buscar?cpf_cnpj=000", headers=auth_headers)
    assert resposta.status_code == 422
    corpo = resposta.json()
    assert corpo["correlation_id"]
    assert corpo["detail"]


def test_cabecalhos_de_seguranca_presentes(client):
    cabecalhos = client.get("/health").headers
    assert cabecalhos["x-content-type-options"] == "nosniff"
    assert cabecalhos["x-frame-options"] == "DENY"
    assert cabecalhos["referrer-policy"] == "no-referrer"
    assert cabecalhos["server"] == "webserver"


def test_liveness_nao_toca_o_banco(client):
    corpo = client.get("/health").json()
    assert corpo["status"] == "ok"
    assert "banco" not in corpo


def test_readiness_confirma_o_banco(client):
    resposta = client.get("/health/ready")
    assert resposta.status_code == 200
    assert resposta.json()["banco"] == "ok"
