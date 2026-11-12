import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.entities.os import ItemOS, OrdemDeServico, StatusOS, TipoItem
from app.domain.exceptions import BusinessRuleException
from app.domain.saga import Comando, EstadoSaga, PassoSaga, PrazosSaga, SagaAberturaOS

AGORA = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
PRAZOS = PrazosSaga()
CLIENTE = {"id": str(uuid.uuid4()), "nome": "Ana", "email": "ana@exemplo.com", "telefone": None}


def nova_os():
    pastilha, revisao = uuid.uuid4(), uuid.uuid4()
    return OrdemDeServico(cliente_id=uuid.uuid4(), veiculo_id=uuid.uuid4(), id=uuid.uuid4(), itens=[
        ItemOS(tipo=TipoItem.PECA, item_id=pastilha, quantidade=2),
        ItemOS(tipo=TipoItem.SERVICO, item_id=revisao, quantidade=1),
    ])


def precos(os):
    itens = [{"tipo": i.tipo.value, "item_id": str(i.item_id), "descricao": "item", "quantidade": i.quantidade,
              "preco_unitario": "50.00", "subtotal": f"{50 * i.quantidade}.00"} for i in os.itens]
    return {"itens": itens, "valor_total": "150.00"}


EVENTOS = {
    "PecasReservadas": precos,
    "OrcamentoGerado": lambda os: {"orcamento_id": "orc-1", "valor_total": "150.00",
                                   "expira_em": (AGORA + timedelta(hours=1)).isoformat()},
    "OrcamentoAprovado": lambda os: {"orcamento_id": "orc-1", "aprovado_por": "CLIENTE"},
    "CobrancaCriada": lambda os: {"pagamento_id": "pag-1", "provedor": "SIMULADO", "valor": "150.00",
                                  "qr_data": "qr", "expira_em": (AGORA + timedelta(minutes=15)).isoformat()},
    "PagamentoConfirmado": lambda os: {"pagamento_id": "pag-1", "valor": "150.00", "pago_em": AGORA.isoformat()},
    "OSEnfileirada": lambda os: {"posicao": 1},
}


def receber(saga, os, tipo, payload=None):
    payload = payload if payload is not None else EVENTOS.get(tipo, lambda os: {})(os)
    return saga.ao_receber(tipo, payload, os, CLIENTE, AGORA, PRAZOS)


def ate(passo_final):
    """Saga e OS levadas pelo caminho feliz até o evento indicado, inclusive."""
    os = nova_os()
    saga, _ = SagaAberturaOS.abrir(os, AGORA, PRAZOS)
    for tipo in ("PecasReservadas", "OrcamentoGerado", "OrcamentoAprovado", "CobrancaCriada",
                 "PagamentoConfirmado", "OSEnfileirada"):
        receber(saga, os, tipo)
        if tipo == passo_final:
            break
    return saga, os


def tipos(comandos):
    return [c.tipo for c in comandos]


def test_abrir_pede_a_reserva_dos_itens_da_os():
    os = nova_os()
    saga, comandos = SagaAberturaOS.abrir(os, AGORA, PRAZOS)

    assert saga.os_id == os.id and saga.passo == PassoSaga.RESERVANDO_PECAS
    assert saga.expira_em == AGORA + PRAZOS.resposta
    assert comandos == [Comando("ReservarPecas", {"itens": [
        {"tipo": "PECA", "item_id": str(os.itens[0].item_id), "quantidade": 2},
        {"tipo": "SERVICO", "item_id": str(os.itens[1].item_id), "quantidade": 1},
    ]})]
    assert comandos[0].rota == "execucao.reservar-pecas"


def test_caminho_feliz_ate_a_os_finalizada():
    os = nova_os()
    saga, _ = SagaAberturaOS.abrir(os, AGORA, PRAZOS)

    gerar = receber(saga, os, "PecasReservadas")
    assert tipos(gerar) == ["GerarOrcamento"] and gerar[0].payload["cliente"] == CLIENTE
    assert os.valor_total == Decimal("150.00") and os.status == StatusOS.ABERTA

    assert receber(saga, os, "OrcamentoGerado") == []
    assert os.status == StatusOS.AGUARDANDO_APROVACAO and saga.orcamento_id == "orc-1"
    assert saga.expira_em == AGORA + timedelta(hours=1) + PRAZOS.folga

    assert receber(saga, os, "OrcamentoAprovado") == [Comando("Cobrar", {"orcamento_id": "orc-1"})]
    assert os.status == StatusOS.AGUARDANDO_PAGAMENTO

    assert receber(saga, os, "CobrancaCriada") == []
    assert saga.pagamento_id == "pag-1" and saga.expira_em == AGORA + timedelta(minutes=15) + PRAZOS.folga

    assert receber(saga, os, "PagamentoConfirmado") == [Comando("EnfileirarOS", {"veiculo_id": str(os.veiculo_id)})]
    assert receber(saga, os, "OSEnfileirada") == []
    assert os.status == StatusOS.NA_FILA and saga.passo == PassoSaga.EM_EXECUCAO and saga.expira_em is None

    for tipo in ("DiagnosticoIniciado", "ExecucaoIniciada", "ExecucaoFinalizada"):
        assert receber(saga, os, tipo) == []
    assert os.status == StatusOS.FINALIZADA and saga.estado == EstadoSaga.CONCLUIDA


def test_execucao_finalizada_direto_da_fila_avanca_todos_os_status():
    saga, os = ate("OSEnfileirada")
    receber(saga, os, "ExecucaoFinalizada")
    receber(saga, os, "ExecucaoFinalizada")

    assert [h.status for h in os.historico][-4:] == [
        StatusOS.NA_FILA, StatusOS.EM_DIAGNOSTICO, StatusOS.EM_EXECUCAO, StatusOS.FINALIZADA]


def test_reserva_recusada_cancela_a_os_sem_compensacoes():
    os = nova_os()
    saga, _ = SagaAberturaOS.abrir(os, AGORA, PRAZOS)

    assert receber(saga, os, "ReservaRecusada", {"motivo": "ESTOQUE_INSUFICIENTE", "itens": []}) == []
    assert saga.estado == EstadoSaga.COMPENSADA and os.status == StatusOS.CANCELADA


@pytest.mark.parametrize("motivo, status_final", [
    ("ORCAMENTO_REJEITADO", StatusOS.NEGADA),
    ("ORCAMENTO_EXPIRADO", StatusOS.CANCELADA),
])
def test_orcamento_rejeitado_libera_a_reserva(motivo, status_final):
    saga, os = ate("OrcamentoGerado")

    comandos = receber(saga, os, "OrcamentoRejeitado", {"orcamento_id": "orc-1", "motivo": motivo})
    assert comandos == [Comando("LiberarReserva", {"motivo": motivo})]
    assert saga.estado == EstadoSaga.COMPENSANDO

    assert receber(saga, os, "ReservaLiberada") == []
    assert saga.estado == EstadoSaga.COMPENSADA and os.status == status_final


def test_pagamento_recusado_cancela_orcamento_e_depois_libera_reserva():
    saga, os = ate("CobrancaCriada")

    assert tipos(receber(saga, os, "PagamentoRecusado", {"pagamento_id": "pag-1", "motivo": "PAGAMENTO_EXPIRADO"})) == ["CancelarOrcamento"]
    assert tipos(receber(saga, os, "OrcamentoCancelado")) == ["LiberarReserva"]
    assert receber(saga, os, "ReservaLiberada") == []
    assert os.status == StatusOS.CANCELADA and saga.motivo == "PAGAMENTO_EXPIRADO"


def test_enfileiramento_recusado_reembolsa_antes_de_desfazer_o_resto():
    saga, os = ate("PagamentoConfirmado")

    enviados = receber(saga, os, "EnfileiramentoRecusado", {"motivo": "RESERVA_INEXISTENTE"})
    for confirmacao in ("PagamentoReembolsado", "OrcamentoCancelado", "ReservaLiberada"):
        enviados += receber(saga, os, confirmacao)

    assert tipos(enviados) == ["Reembolsar", "CancelarOrcamento", "LiberarReserva"]
    assert all(c.payload == {"motivo": "ENFILEIRAMENTO_RECUSADO"} for c in enviados)
    assert saga.estado == EstadoSaga.COMPENSADA and os.status == StatusOS.CANCELADA
    assert not (saga.reserva_feita or saga.orcamento_ativo or saga.pagamento_confirmado)


def test_reembolso_que_falha_para_a_saga_para_acao_humana():
    saga, os = ate("PagamentoConfirmado")
    receber(saga, os, "EnfileiramentoRecusado", {"motivo": "RESERVA_INEXISTENTE"})

    assert receber(saga, os, "ReembolsoFalhou", {"pagamento_id": "pag-1", "detalhe": "409"}) == []
    assert saga.estado == EstadoSaga.FALHA_NA_COMPENSACAO and saga.expira_em is None
    assert os.status == StatusOS.AGUARDANDO_PAGAMENTO


def test_eventos_fora_de_ordem_ou_repetidos_sao_descartados():
    saga, os = ate("OrcamentoGerado")
    assert receber(saga, os, "PagamentoConfirmado") is None
    assert receber(saga, os, "PecasReservadas") is None
    assert receber(saga, os, "ExecucaoFinalizada") is None

    receber(saga, os, "OrcamentoRejeitado", {"orcamento_id": "orc-1", "motivo": "ORCAMENTO_REJEITADO"})
    assert receber(saga, os, "OrcamentoCancelado") is None
    receber(saga, os, "ReservaLiberada")
    assert receber(saga, os, "ReservaLiberada") is None
    assert receber(saga, os, "OrcamentoAprovado") is None


@pytest.mark.parametrize("passo_final, compensacoes", [
    (None, ["LiberarReserva"]),
    ("PecasReservadas", ["CancelarOrcamento", "LiberarReserva"]),
    ("OrcamentoGerado", ["CancelarOrcamento", "LiberarReserva"]),
    ("CobrancaCriada", ["CancelarOrcamento", "LiberarReserva"]),
])
def test_prazo_esgotado_antes_do_pagamento_desfaz_por_garantia(passo_final, compensacoes):
    if passo_final:
        saga, os = ate(passo_final)
    else:
        os = nova_os()
        saga, _ = SagaAberturaOS.abrir(os, AGORA, PRAZOS)

    assert tipos(saga.ao_expirar(os, AGORA, PRAZOS)) == compensacoes[:1]
    assert saga.compensacoes == compensacoes and saga.motivo == "PRAZO_ESGOTADO"


def test_prazo_esgotado_depois_do_pagamento_tenta_enfileirar_de_novo():
    saga, os = ate("PagamentoConfirmado")

    for tentativa in range(1, 4):
        assert tipos(saga.ao_expirar(os, AGORA, PRAZOS)) == ["EnfileirarOS"]
        assert saga.tentativas == tentativa
    assert tipos(saga.ao_expirar(os, AGORA, PRAZOS)) == ["Reembolsar"]


def test_prazo_esgotado_na_compensacao_reenvia_o_mesmo_comando():
    saga, os = ate("CobrancaCriada")
    receber(saga, os, "PagamentoRecusado", {"pagamento_id": "pag-1", "motivo": "PAGAMENTO_RECUSADO"})

    assert saga.ao_expirar(os, AGORA + timedelta(minutes=10), PRAZOS) == [
        Comando("CancelarOrcamento", {"motivo": "PAGAMENTO_RECUSADO"})]
    assert saga.expira_em == AGORA + timedelta(minutes=10) + PRAZOS.resposta


def test_prazo_nao_se_aplica_durante_a_execucao():
    saga, os = ate("OSEnfileirada")
    assert saga.ao_expirar(os, AGORA, PRAZOS) == [] and saga.estado == EstadoSaga.EM_ANDAMENTO


def test_oficina_cancela_enquanto_aguarda_aprovacao():
    saga, os = ate("OrcamentoGerado")

    assert tipos(saga.cancelar_pela_oficina(os, AGORA, PRAZOS)) == ["CancelarOrcamento"]
    assert saga.motivo == "CANCELADA_PELA_OFICINA"


def test_oficina_nao_cancela_depois_do_pagamento():
    saga, os = ate("PagamentoConfirmado")
    with pytest.raises(BusinessRuleException):
        saga.cancelar_pela_oficina(os, AGORA, PRAZOS)


def test_pagamento_atrasado_durante_a_compensacao_entra_na_fila_de_reembolso():
    saga, os = ate("CobrancaCriada")
    saga.cancelar_pela_oficina(os, AGORA, PRAZOS)

    assert receber(saga, os, "PagamentoConfirmado") == []
    assert saga.compensacoes == ["CancelarOrcamento", "LiberarReserva", "Reembolsar"]
    assert receber(saga, os, "PagamentoConfirmado") is None


def test_pagamento_atrasado_depois_da_saga_desfeita_reabre_para_reembolsar():
    saga, os = ate("CobrancaCriada")
    receber(saga, os, "PagamentoRecusado", {"pagamento_id": "pag-1", "motivo": "PAGAMENTO_EXPIRADO"})
    receber(saga, os, "OrcamentoCancelado")
    receber(saga, os, "ReservaLiberada")
    assert saga.estado == EstadoSaga.COMPENSADA

    assert tipos(receber(saga, os, "PagamentoConfirmado")) == ["Reembolsar"]
    assert receber(saga, os, "PagamentoReembolsado") == []
    assert saga.estado == EstadoSaga.COMPENSADA and os.status == StatusOS.CANCELADA
