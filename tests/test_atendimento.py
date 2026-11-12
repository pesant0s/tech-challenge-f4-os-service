import logging
import uuid

import pytest

from app.adapters.outbound.persistence.os_repository import OSRepositoryAdapter
from app.domain.entities.cliente import Cliente
from app.domain.entities.os import StatusOS

CPF_AT_1 = "529.982.247-25"
CPF_AT_2 = "123.456.789-09"


def _cliente_e_veiculo(client, auth_headers, cpf=CPF_AT_1, placa="XYZ-9999"):
    cliente = client.post("/cadastro/clientes", json={
        "nome": "Pedro", "cpf_cnpj": cpf, "telefone": "11999999999"
    }, headers=auth_headers).json()
    veiculo = client.post("/cadastro/veiculos", json={
        "placa": placa, "marca": "Honda", "modelo": "Civic",
        "ano": "2021", "cliente_id": cliente["id"]
    }, headers=auth_headers).json()
    return cliente, veiculo


def _corpo(cliente, veiculo, **itens):
    itens = itens or {"servicos": [{"servico_id": str(uuid.uuid4()), "quantidade": 1}]}
    return {"cliente_id": cliente["id"], "veiculo_id": veiculo["id"], **itens}


def _abrir_os(client, auth_headers, **itens):
    cliente, veiculo = _cliente_e_veiculo(client, auth_headers)
    return client.post("/atendimento/os", json=_corpo(cliente, veiculo, **itens), headers=auth_headers).json()


def _levar_para(db, os_id, *status):
    """Não há rota de status: os eventos dos outros serviços são simulados direto no domínio."""
    repo = OSRepositoryAdapter(db)
    os = repo.buscar_para_escrita(uuid.UUID(os_id))
    for s in status:
        os.transicionar_para(s)
    repo.commit()


CAMINHO_ATE_FINALIZADA = [
    StatusOS.AGUARDANDO_APROVACAO, StatusOS.AGUARDANDO_PAGAMENTO, StatusOS.NA_FILA,
    StatusOS.EM_DIAGNOSTICO, StatusOS.EM_EXECUCAO, StatusOS.FINALIZADA,
]


def test_criar_os(client, auth_headers):
    cliente, veiculo = _cliente_e_veiculo(client, auth_headers)
    servico_id, peca_id = str(uuid.uuid4()), str(uuid.uuid4())
    r = client.post("/atendimento/os", json=_corpo(
        cliente, veiculo,
        servicos=[{"servico_id": servico_id, "quantidade": 1}],
        pecas=[{"peca_id": peca_id, "quantidade": 2}],
    ), headers=auth_headers)
    assert r.status_code == 201
    corpo = r.json()
    assert corpo["status"] == "ABERTA"
    assert corpo["valor_total"] is None
    assert [h["status"] for h in corpo["historico"]] == ["ABERTA"]
    itens = {(i["tipo"], i["item_id"]): i for i in corpo["itens"]}
    assert set(itens) == {("SERVICO", servico_id), ("PECA", peca_id)}
    assert itens[("PECA", peca_id)]["quantidade"] == 2
    for item in itens.values():
        assert item["descricao"] is None
        assert item["preco_unitario"] is None


def test_criar_os_com_peca_apenas(client, auth_headers):
    cliente, veiculo = _cliente_e_veiculo(client, auth_headers)
    r = client.post("/atendimento/os", json=_corpo(
        cliente, veiculo, pecas=[{"peca_id": str(uuid.uuid4()), "quantidade": 1}]
    ), headers=auth_headers)
    assert r.status_code == 201
    assert [i["tipo"] for i in r.json()["itens"]] == ["PECA"]


def test_criar_os_sem_itens(client, auth_headers):
    cliente, veiculo = _cliente_e_veiculo(client, auth_headers)
    r = client.post("/atendimento/os", json=_corpo(cliente, veiculo, servicos=[], pecas=[]), headers=auth_headers)
    assert r.status_code == 422


def test_criar_os_cliente_inexistente(client, auth_headers):
    cliente, veiculo = _cliente_e_veiculo(client, auth_headers)
    r = client.post("/atendimento/os", json=_corpo({"id": str(uuid.uuid4())}, veiculo), headers=auth_headers)
    assert r.status_code == 404


def test_criar_os_cliente_inativo(client, db, auth_headers):
    cliente, veiculo = _cliente_e_veiculo(client, auth_headers)
    db.get(Cliente, uuid.UUID(cliente["id"])).ativo = False
    db.commit()
    r = client.post("/atendimento/os", json=_corpo(cliente, veiculo), headers=auth_headers)
    assert r.status_code == 422
    assert "inativo" in r.json()["detail"]


def test_criar_os_veiculo_de_outro_cliente(client, auth_headers):
    cliente, _ = _cliente_e_veiculo(client, auth_headers)
    _, veiculo_alheio = _cliente_e_veiculo(client, auth_headers, cpf=CPF_AT_2, placa="ABC-1234")
    r = client.post("/atendimento/os", json=_corpo(cliente, veiculo_alheio), headers=auth_headers)
    assert r.status_code == 422


def test_criar_os_veiculo_inexistente(client, auth_headers):
    cliente, _ = _cliente_e_veiculo(client, auth_headers)
    r = client.post("/atendimento/os", json=_corpo(cliente, {"id": str(uuid.uuid4())}), headers=auth_headers)
    assert r.status_code == 422


def test_os_requer_auth(client):
    r = client.post("/atendimento/os", json={
        "cliente_id": "00000000-0000-0000-0000-000000000001",
        "veiculo_id": "00000000-0000-0000-0000-000000000002",
        "servicos": [], "pecas": []
    })
    assert r.status_code == 401


def test_cliente_consulta_as_proprias_os(client, auth_headers, token_cliente):
    cliente, veiculo = _cliente_e_veiculo(client, auth_headers)
    client.post("/atendimento/os", json=_corpo(cliente, veiculo), headers=auth_headers)
    r = client.get("/atendimento/os/consulta", headers=token_cliente(cliente))
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_listar_os(client, auth_headers):
    _abrir_os(client, auth_headers)
    r = client.get("/atendimento/os", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_listar_os_filtro_status(client, db, auth_headers):
    os = _abrir_os(client, auth_headers)
    _levar_para(db, os["id"], StatusOS.AGUARDANDO_APROVACAO)

    r_aguardando = client.get("/atendimento/os?status=AGUARDANDO_APROVACAO", headers=auth_headers)
    assert [o["id"] for o in r_aguardando.json()] == [os["id"]]
    assert client.get("/atendimento/os?status=ABERTA", headers=auth_headers).json() == []


def test_funcionario_consulta_qualquer_os_por_id(client, auth_headers):
    os = _abrir_os(client, auth_headers)
    r = client.get(f"/atendimento/os/{os['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["id"] == os["id"]
    assert r.json()["status"] == "ABERTA"


def test_cliente_consulta_a_propria_os_por_id(client, auth_headers, token_cliente):
    cliente, veiculo = _cliente_e_veiculo(client, auth_headers)
    os = client.post("/atendimento/os", json=_corpo(cliente, veiculo), headers=auth_headers).json()
    r = client.get(f"/atendimento/os/{os['id']}", headers=token_cliente(cliente))
    assert r.status_code == 200
    assert r.json()["id"] == os["id"]


def test_get_os_nao_encontrada(client, auth_headers):
    r = client.get("/atendimento/os/00000000-0000-0000-0000-000000000000", headers=auth_headers)
    assert r.status_code == 404


def test_entregar_os_finalizada(client, db, auth_headers):
    os = _abrir_os(client, auth_headers)
    _levar_para(db, os["id"], *CAMINHO_ATE_FINALIZADA)
    r = client.post(f"/atendimento/os/{os['id']}/entregar", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "ENTREGUE"
    assert [h["status"] for h in r.json()["historico"]] == [
        "ABERTA", "AGUARDANDO_APROVACAO", "AGUARDANDO_PAGAMENTO", "NA_FILA",
        "EM_DIAGNOSTICO", "EM_EXECUCAO", "FINALIZADA", "ENTREGUE",
    ]


@pytest.mark.parametrize("anteriores", [
    [],
    [StatusOS.AGUARDANDO_APROVACAO, StatusOS.AGUARDANDO_PAGAMENTO, StatusOS.NA_FILA],
    [StatusOS.AGUARDANDO_APROVACAO, StatusOS.NEGADA],
], ids=["aberta", "na fila", "negada"])
def test_entregar_os_de_outro_status_e_recusado(client, db, auth_headers, anteriores):
    os = _abrir_os(client, auth_headers)
    _levar_para(db, os["id"], *anteriores)
    r = client.post(f"/atendimento/os/{os['id']}/entregar", headers=auth_headers)
    assert r.status_code == 422
    assert "Transição inválida" in r.json()["detail"]


def test_entregar_os_inexistente(client, auth_headers):
    assert client.post(f"/atendimento/os/{uuid.uuid4()}/entregar", headers=auth_headers).status_code == 404


def test_entregar_exige_funcionario(client, auth_headers, token_cliente):
    cliente, veiculo = _cliente_e_veiculo(client, auth_headers)
    os = client.post("/atendimento/os", json=_corpo(cliente, veiculo), headers=auth_headers).json()
    url = f"/atendimento/os/{os['id']}/entregar"
    assert client.post(url).status_code == 401
    assert client.post(url, headers=token_cliente(cliente)).status_code == 403


@pytest.mark.parametrize("metodo,rota", [
    ("patch", "/atendimento/os/{os}/status"),
    ("post", "/atendimento/os/{os}/aprovar"),
    ("post", "/atendimento/os/{os}/rejeitar"),
    ("get", "/atendimento/os/fila"),
])
def test_rotas_removidas_nao_existem_mais(client, auth_headers, metodo, rota):
    os = _abrir_os(client, auth_headers)
    r = getattr(client, metodo)(rota.format(os=os["id"]), headers=auth_headers)
    assert r.status_code in (404, 405, 422)
    assert r.status_code != 200


def test_metricas_sem_os_finalizada(client, auth_headers):
    r = client.get("/atendimento/os/metricas/tempo-medio", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["total_os_finalizadas"] == 0
    assert r.json()["tempo_medio_minutos"] == 0
    assert r.json()["por_status"] == {}


def test_metricas_tempo_medio(client, db, auth_headers):
    os = _abrir_os(client, auth_headers)
    _levar_para(db, os["id"], *CAMINHO_ATE_FINALIZADA)
    r = client.get("/atendimento/os/metricas/tempo-medio", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["total_os_finalizadas"] == 1
    assert r.json()["tempo_medio_minutos"] >= 0
    assert set(r.json()["por_status"]) == {
        "ABERTA", "AGUARDANDO_APROVACAO", "AGUARDANDO_PAGAMENTO", "NA_FILA", "EM_DIAGNOSTICO", "EM_EXECUCAO",
    }


def test_abertura_e_entrega_geram_evento_para_os_dashboards(client, db, auth_headers, caplog):
    with caplog.at_level(logging.INFO, logger="oficina.os"):
        os = _abrir_os(client, auth_headers)
        _levar_para(db, os["id"], *CAMINHO_ATE_FINALIZADA)
        client.post(f"/atendimento/os/{os['id']}/entregar", headers=auth_headers)
    eventos = [e for e in caplog.records if getattr(e, "evento", None) == "os_status"]
    assert [(e.status_anterior, e.status_novo) for e in eventos] == [
        (None, "ABERTA"), ("FINALIZADA", "ENTREGUE"),
    ]
    assert eventos[0].segundos_no_status_anterior is None
    assert eventos[1].segundos_no_status_anterior >= 0
