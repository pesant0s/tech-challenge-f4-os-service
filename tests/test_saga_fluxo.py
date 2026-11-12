"""A Saga pela API e pelos casos de uso, com o banco de testes e sem broker."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.adapters.outbound.persistence.cliente_repository import ClienteRepositoryAdapter
from app.adapters.outbound.persistence.os_repository import OSRepositoryAdapter
from app.adapters.outbound.persistence.outbox_repository import InboxRepositoryAdapter, OutboxRepositoryAdapter
from app.adapters.outbound.persistence.saga_repository import SagaRepositoryAdapter
from app.application.use_cases.saga_os import ProcessarEventoSagaUseCase, VerificarPrazosSagaUseCase
from app.domain.saga import PrazosSaga
from app.infrastructure.orm_mapping import outbox

PRAZOS = PrazosSaga()


@pytest.fixture
def os_aberta(client, auth_headers):
    cliente = client.post("/cadastro/clientes", headers=auth_headers, json={
        "nome": "Ana Souza", "cpf_cnpj": "52998224725", "telefone": "11999990000", "email": "ana@exemplo.com",
    }).json()
    veiculo = client.post("/cadastro/veiculos", headers=auth_headers, json={
        "placa": "ABC1D23", "marca": "Fiat", "modelo": "Uno", "ano": "2020", "cliente_id": cliente["id"],
    }).json()
    resposta = client.post("/atendimento/os", headers=auth_headers, json={
        "cliente_id": cliente["id"], "veiculo_id": veiculo["id"],
        "servicos": [{"servico_id": str(uuid.uuid4()), "quantidade": 1}],
        "pecas": [{"peca_id": str(uuid.uuid4()), "quantidade": 2}],
    })
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def mensagens(db):
    return [linha.corpo for linha in db.execute(select(outbox).order_by(outbox.c.criado_em)).all()]


def evento(os, tipo, payload, message_id=None):
    return {"message_id": message_id or str(uuid.uuid4()), "tipo": tipo, "versao": 1,
            "saga_id": os["saga"]["id"], "os_id": os["id"], "ocorrido_em": "2026-11-12T15:00:00Z",
            "origem": "execucao-service", "payload": payload}


def processar(db, envelope):
    return ProcessarEventoSagaUseCase(
        SagaRepositoryAdapter(db), OSRepositoryAdapter(db), ClienteRepositoryAdapter(db),
        OutboxRepositoryAdapter(db), InboxRepositoryAdapter(db), PRAZOS,
    ).executar(envelope)


def pecas_reservadas(os):
    itens = [{"tipo": i["tipo"], "item_id": i["item_id"], "descricao": "Item", "quantidade": i["quantidade"],
              "preco_unitario": "50.00", "subtotal": f"{50 * i['quantidade']}.00"} for i in os["itens"]]
    return {"itens": itens, "valor_total": "150.00"}


def test_abrir_a_os_inicia_a_saga_e_grava_o_comando_no_outbox(os_aberta, db):
    assert os_aberta["status"] == "ABERTA"
    assert os_aberta["saga"]["passo"] == "RESERVANDO_PECAS" and os_aberta["saga"]["estado"] == "EM_ANDAMENTO"

    [comando] = mensagens(db)
    assert comando["tipo"] == "ReservarPecas" and comando["saga_id"] == os_aberta["saga"]["id"]
    assert {i["tipo"] for i in comando["payload"]["itens"]} == {"SERVICO", "PECA"}
    linha = db.execute(select(outbox)).one()
    assert linha.rota == "execucao.reservar-pecas" and linha.exchange == "oficina.comandos" and linha.publicado_em is None


def test_pecas_reservadas_precificam_a_os_e_pedem_o_orcamento_com_os_dados_do_cliente(client, auth_headers, os_aberta, db):
    assert processar(db, evento(os_aberta, "PecasReservadas", pecas_reservadas(os_aberta))) is True

    os = client.get(f"/atendimento/os/{os_aberta['id']}", headers=auth_headers).json()
    assert os["valor_total"] == "150.00" and all(i["preco_unitario"] == "50.00" for i in os["itens"])
    assert os["saga"]["passo"] == "GERANDO_ORCAMENTO"
    gerar = mensagens(db)[-1]
    assert gerar["tipo"] == "GerarOrcamento"
    assert gerar["payload"]["cliente"] == {"id": os["cliente_id"], "nome": "Ana Souza",
                                           "email": "ana@exemplo.com", "telefone": "11999990000"}


def test_mensagem_repetida_nao_tem_efeito(os_aberta, db):
    envelope = evento(os_aberta, "PecasReservadas", pecas_reservadas(os_aberta))
    assert processar(db, envelope) is True
    assert processar(db, envelope) is False
    assert [m["tipo"] for m in mensagens(db)] == ["ReservarPecas", "GerarOrcamento"]


def test_evento_fora_do_passo_e_registrado_e_descartado(client, auth_headers, os_aberta, db):
    assert processar(db, evento(os_aberta, "OrcamentoAprovado", {"orcamento_id": "o", "aprovado_por": "CLIENTE"})) is True
    os = client.get(f"/atendimento/os/{os_aberta['id']}", headers=auth_headers).json()
    assert os["status"] == "ABERTA" and len(mensagens(db)) == 1


def test_estoque_insuficiente_cancela_a_os(client, auth_headers, os_aberta, db):
    processar(db, evento(os_aberta, "ReservaRecusada", {"motivo": "ESTOQUE_INSUFICIENTE", "itens": []}))
    os = client.get(f"/atendimento/os/{os_aberta['id']}", headers=auth_headers).json()
    assert os["status"] == "CANCELADA"
    assert os["saga"]["estado"] == "COMPENSADA" and os["saga"]["motivo"] == "RESERVA_RECUSADA"


def test_oficina_cancela_e_a_saga_compensa(client, auth_headers, os_aberta, db):
    processar(db, evento(os_aberta, "PecasReservadas", pecas_reservadas(os_aberta)))

    resposta = client.post(f"/atendimento/os/{os_aberta['id']}/cancelar", headers=auth_headers)
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["saga"]["estado"] == "COMPENSANDO"
    assert resposta.json()["saga"]["compensacoes"] == ["CancelarOrcamento", "LiberarReserva"]
    assert mensagens(db)[-1]["tipo"] == "CancelarOrcamento"

    processar(db, evento(os_aberta, "OrcamentoCancelado", {"orcamento_id": "o"}))
    processar(db, evento(os_aberta, "ReservaLiberada", {}))
    os = client.get(f"/atendimento/os/{os_aberta['id']}", headers=auth_headers).json()
    assert os["status"] == "CANCELADA" and os["saga"]["estado"] == "COMPENSADA"
    assert client.post(f"/atendimento/os/{os_aberta['id']}/cancelar", headers=auth_headers).status_code == 422


def test_cancelar_os_inexistente_responde_404(client, auth_headers):
    assert client.post(f"/atendimento/os/{uuid.uuid4()}/cancelar", headers=auth_headers).status_code == 404


def test_cliente_nao_cancela_pela_oficina(client, os_aberta, token_cliente):
    resposta = client.post(f"/atendimento/os/{os_aberta['id']}/cancelar",
                           headers=token_cliente({"id": os_aberta["cliente_id"], "cpf_cnpj": "52998224725", "nome": "Ana Souza"}))
    assert resposta.status_code in (401, 403)


def test_varredura_de_prazos_desfaz_a_saga_parada(client, auth_headers, os_aberta, db):
    depois = datetime.now(timezone.utc) + timedelta(hours=1)
    vencidas = VerificarPrazosSagaUseCase(SagaRepositoryAdapter(db), OSRepositoryAdapter(db),
                                          OutboxRepositoryAdapter(db), PRAZOS).executar(depois)
    assert vencidas == 1
    os = client.get(f"/atendimento/os/{os_aberta['id']}", headers=auth_headers).json()
    assert os["saga"]["motivo"] == "PRAZO_ESGOTADO"
    assert mensagens(db)[-1]["tipo"] == "LiberarReserva"


def test_varredura_sem_saga_vencida_nao_faz_nada(os_aberta, db):
    assert VerificarPrazosSagaUseCase(SagaRepositoryAdapter(db), OSRepositoryAdapter(db),
                                      OutboxRepositoryAdapter(db), PRAZOS).executar() == 0
