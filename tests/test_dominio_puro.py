"""Agregado OrdemDeServico sem banco, HTTP ou ORM: quebra aqui se o domínio voltar a depender da infraestrutura."""
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.os import ItemOS, OrdemDeServico, StatusOS, TipoItem
from app.domain.exceptions import BusinessRuleException

S = StatusOS

TRANSICOES = {
    S.ABERTA: {S.AGUARDANDO_APROVACAO, S.CANCELADA},
    S.AGUARDANDO_APROVACAO: {S.AGUARDANDO_PAGAMENTO, S.NEGADA, S.CANCELADA},
    S.AGUARDANDO_PAGAMENTO: {S.NA_FILA, S.CANCELADA},
    S.NA_FILA: {S.EM_DIAGNOSTICO},
    S.EM_DIAGNOSTICO: {S.EM_EXECUCAO},
    S.EM_EXECUCAO: {S.FINALIZADA},
    S.FINALIZADA: {S.ENTREGUE},
    S.ENTREGUE: set(),
    S.NEGADA: set(),
    S.CANCELADA: set(),
}


def _precificado(item: ItemOS, preco: str, descricao: str = "Item") -> dict:
    return {
        "tipo": item.tipo.value, "item_id": str(item.item_id), "descricao": descricao,
        "quantidade": item.quantidade, "preco_unitario": preco,
        "subtotal": str(Decimal(preco) * item.quantidade),
    }


def test_statusos_tem_os_dez_estados_na_ordem():
    assert [s.value for s in StatusOS] == [
        "ABERTA", "AGUARDANDO_APROVACAO", "AGUARDANDO_PAGAMENTO", "NA_FILA", "EM_DIAGNOSTICO",
        "EM_EXECUCAO", "FINALIZADA", "ENTREGUE", "NEGADA", "CANCELADA",
    ]


def test_os_nova_nasce_aberta_com_itens_vazios_e_historico_inicial():
    os_teste = OrdemDeServico()
    assert os_teste.status == StatusOS.ABERTA
    assert os_teste.itens == []
    assert [h.status for h in os_teste.historico] == [StatusOS.ABERTA]


def test_mapa_de_transicoes_cobre_todos_os_estados():
    assert {k: set(v) for k, v in OrdemDeServico.TRANSICOES_VALIDAS.items()} == TRANSICOES


@pytest.mark.parametrize("origem,destino", [(o, d) for o, ds in TRANSICOES.items() for d in ds])
def test_transicao_valida(origem, destino):
    os_teste = OrdemDeServico(status=origem)
    os_teste.transicionar_para(destino)
    assert os_teste.status == destino
    assert os_teste.historico[-1].status == destino


@pytest.mark.parametrize("origem,destino", [(o, d) for o in StatusOS for d in StatusOS if d not in TRANSICOES[o]])
def test_transicao_invalida_e_recusada(origem, destino):
    os_teste = OrdemDeServico(status=origem)
    with pytest.raises(BusinessRuleException, match="Transição inválida"):
        os_teste.transicionar_para(destino)
    assert os_teste.status == origem
    assert len(os_teste.historico) == 1


def test_fluxo_completo_da_os_em_memoria():
    os_teste = OrdemDeServico()
    for status in [S.AGUARDANDO_APROVACAO, S.AGUARDANDO_PAGAMENTO, S.NA_FILA, S.EM_DIAGNOSTICO,
                   S.EM_EXECUCAO, S.FINALIZADA, S.ENTREGUE]:
        os_teste.transicionar_para(status)
    assert [h.status.value for h in os_teste.historico] == [
        "ABERTA", "AGUARDANDO_APROVACAO", "AGUARDANDO_PAGAMENTO", "NA_FILA",
        "EM_DIAGNOSTICO", "EM_EXECUCAO", "FINALIZADA", "ENTREGUE",
    ]
    assert os_teste.historico[-2].entrou_em == os_teste.finalizado_em
    assert os_teste.finalizado_em >= os_teste.iniciado_em


def test_subtotal_do_item_preserva_precisao_decimal():
    item = ItemOS(tipo=TipoItem.PECA, item_id=uuid4(), quantidade=3, preco_unitario=Decimal("0.10"))
    assert item.calcular_subtotal() == Decimal("0.30")


def test_subtotal_de_item_sem_preco_e_recusado():
    with pytest.raises(BusinessRuleException, match="não precificado"):
        ItemOS(tipo=TipoItem.SERVICO, item_id=uuid4()).calcular_subtotal()


def test_precificar_preenche_descricao_preco_e_total():
    peca = ItemOS(tipo=TipoItem.PECA, item_id=uuid4(), quantidade=2)
    servico = ItemOS(tipo=TipoItem.SERVICO, item_id=uuid4())
    os_teste = OrdemDeServico(itens=[peca, servico])
    os_teste.precificar([_precificado(peca, "75.00", "Filtro"), _precificado(servico, "150.00", "Revisão")], "300.00")
    assert (peca.descricao, peca.preco_unitario) == ("Filtro", Decimal("75.00"))
    assert (servico.descricao, servico.preco_unitario) == ("Revisão", Decimal("150.00"))
    assert os_teste.valor_total == Decimal("300.00")
    assert peca.calcular_subtotal() == Decimal("150.00")


def test_precificar_item_inexistente_e_recusado():
    item = ItemOS(tipo=TipoItem.PECA, item_id=uuid4())
    os_teste = OrdemDeServico(itens=[item])
    estranho = {**_precificado(item, "10.00"), "item_id": str(uuid4())}
    with pytest.raises(BusinessRuleException, match="não pertence à OS"):
        os_teste.precificar([estranho], "10.00")
    assert os_teste.valor_total is None


def test_precificar_distingue_tipo_do_item():
    item = ItemOS(tipo=TipoItem.PECA, item_id=uuid4())
    os_teste = OrdemDeServico(itens=[item])
    como_servico = {**_precificado(item, "10.00"), "tipo": "SERVICO"}
    with pytest.raises(BusinessRuleException):
        os_teste.precificar([como_servico], "10.00")


def test_avancar_execucao_de_na_fila_direto_a_finalizada():
    os_teste = OrdemDeServico(status=S.NA_FILA)
    os_teste.avancar_execucao(S.FINALIZADA)
    assert os_teste.status == S.FINALIZADA
    assert [h.status for h in os_teste.historico] == [S.NA_FILA, S.EM_DIAGNOSTICO, S.EM_EXECUCAO, S.FINALIZADA]
    assert os_teste.iniciado_em is not None and os_teste.finalizado_em is not None


def test_avancar_execucao_para_em_execucao_para_ali():
    os_teste = OrdemDeServico(status=S.EM_DIAGNOSTICO)
    os_teste.avancar_execucao(S.EM_EXECUCAO)
    assert os_teste.status == S.EM_EXECUCAO
    assert os_teste.finalizado_em is None


def test_avancar_execucao_repetido_e_idempotente():
    os_teste = OrdemDeServico(status=S.NA_FILA)
    os_teste.avancar_execucao(S.EM_EXECUCAO)
    historico = len(os_teste.historico)
    os_teste.avancar_execucao(S.EM_EXECUCAO)
    os_teste.avancar_execucao(S.EM_DIAGNOSTICO)
    assert os_teste.status == S.EM_EXECUCAO
    assert len(os_teste.historico) == historico


@pytest.mark.parametrize("status", [S.ABERTA, S.AGUARDANDO_APROVACAO, S.AGUARDANDO_PAGAMENTO,
                                    S.ENTREGUE, S.NEGADA, S.CANCELADA])
def test_avancar_execucao_fora_do_caminho_e_recusado(status):
    os_teste = OrdemDeServico(status=status)
    with pytest.raises(BusinessRuleException, match="caminho de execução"):
        os_teste.avancar_execucao(S.FINALIZADA)
    assert os_teste.status == status


def test_avancar_execucao_para_alvo_fora_do_caminho_e_recusado():
    with pytest.raises(BusinessRuleException):
        OrdemDeServico(status=S.NA_FILA).avancar_execucao(S.ENTREGUE)
