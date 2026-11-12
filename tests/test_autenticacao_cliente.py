"""Autenticação por CPF: o claim tipo separa cliente de funcionário."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.domain.entities.cliente import Cliente

CPF_A = "529.982.247-25"
CPF_B = "123.456.789-09"


@pytest.fixture
def cenario(client, auth_headers):
    """Dois clientes, cada um com uma OS aberta."""

    def _cliente_com_os(nome, cpf, placa, telefone):
        cliente = client.post("/cadastro/clientes", json={
            "nome": nome, "cpf_cnpj": cpf, "telefone": telefone
        }, headers=auth_headers).json()
        veiculo = client.post("/cadastro/veiculos", json={
            "placa": placa, "marca": "Fiat", "modelo": "Argo",
            "ano": "2022", "cliente_id": cliente["id"]
        }, headers=auth_headers).json()
        os = client.post("/atendimento/os", json={
            "cliente_id": cliente["id"], "veiculo_id": veiculo["id"],
            "servicos": [{"servico_id": str(uuid.uuid4()), "quantidade": 1}]
        }, headers=auth_headers).json()
        return cliente, os

    a, os_a = _cliente_com_os("Ana", CPF_A, "ABC-1234", "11911111111")
    b, os_b = _cliente_com_os("Bruno", CPF_B, "DEF-5678", "11922222222")
    return {"a": a, "os_a": os_a, "b": b, "os_b": os_b}


@pytest.mark.parametrize("metodo,rota", [
    ("get", "/atendimento/os/consulta"),
    ("get", "/atendimento/os/{os}"),
])
def test_rotas_de_cliente_exigem_token(client, cenario, metodo, rota):
    resposta = getattr(client, metodo)(rota.format(os=cenario["os_a"]["id"]))
    assert resposta.status_code == 401


def test_cliente_so_enxerga_as_proprias_os(client, cenario, token_cliente):
    ids_a = {o["id"] for o in client.get(
        "/atendimento/os/consulta", headers=token_cliente(cenario["a"])).json()}
    ids_b = {o["id"] for o in client.get(
        "/atendimento/os/consulta", headers=token_cliente(cenario["b"])).json()}
    assert ids_a == {cenario["os_a"]["id"]}
    assert ids_b == {cenario["os_b"]["id"]}


def test_os_de_outro_cliente_responde_como_inexistente(client, cenario, token_cliente):
    headers = token_cliente(cenario["a"])
    alheia = client.get(f"/atendimento/os/{cenario['os_b']['id']}", headers=headers)
    inexistente = client.get(f"/atendimento/os/{uuid.uuid4()}", headers=headers)
    assert alheia.status_code == inexistente.status_code == 404
    assert alheia.json()["detail"] == inexistente.json()["detail"]


def test_funcionario_enxerga_a_os_de_qualquer_cliente(client, cenario, auth_headers):
    for chave in ("os_a", "os_b"):
        r = client.get(f"/atendimento/os/{cenario[chave]['id']}", headers=auth_headers)
        assert r.status_code == 200


@pytest.mark.parametrize("rota", [
    "/atendimento/os",
    "/cadastro/clientes",
])
def test_token_de_cliente_nao_abre_rotas_da_oficina(client, cenario, token_cliente, rota):
    assert client.get(rota, headers=token_cliente(cenario["a"])).status_code == 403


def test_cliente_nao_entrega_a_propria_os(client, cenario, token_cliente):
    r = client.post(f"/atendimento/os/{cenario['os_a']['id']}/entregar", headers=token_cliente(cenario["a"]))
    assert r.status_code == 403


def test_token_de_funcionario_nao_consulta_como_cliente(client, cenario, auth_headers):
    assert client.get("/atendimento/os/consulta", headers=auth_headers).status_code == 403


def test_token_de_funcionario_declara_o_tipo(client):
    token = client.post("/auth/token", data={
        "username": "admin", "password": "admin123"
    }).json()["access_token"]
    assert jwt.get_unverified_claims(token)["tipo"] == "usuario"


def test_cliente_desativado_perde_o_acesso_mesmo_com_token_valido(client, db, cenario, token_cliente):
    headers = token_cliente(cenario["a"])
    assert client.get("/atendimento/os/consulta", headers=headers).status_code == 200

    cliente = db.get(Cliente, uuid.UUID(cenario["a"]["id"]))
    cliente.ativo = False
    db.commit()

    assert client.get("/atendimento/os/consulta", headers=headers).status_code == 401


def test_token_expirado(client, cenario, token_cliente):
    headers = token_cliente(cenario["a"], expira_em_minutos=-1)
    assert client.get("/atendimento/os/consulta", headers=headers).status_code == 401


def test_token_assinado_com_outra_chave(client, cenario):
    forjado = jwt.encode({
        "sub": cenario["a"]["id"],
        "tipo": "cliente",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }, "chave-de-quem-nao-deveria-conhecer-a-real", algorithm="HS256")
    headers = {"Authorization": f"Bearer {forjado}"}
    assert client.get("/atendimento/os/consulta", headers=headers).status_code == 401


def test_token_de_cliente_inexistente(client, cenario, token_cliente):
    fantasma = {**cenario["a"], "id": str(uuid.uuid4())}
    assert client.get("/atendimento/os/consulta", headers=token_cliente(fantasma)).status_code == 401


def test_sub_malformado_vira_401_e_nao_500(client, cenario, token_cliente):
    """`sub` que não é UUID chegava cru ao driver do banco e derrubava a requisição."""
    headers = token_cliente(cenario["a"], sub="nao-sou-um-uuid")
    assert client.get("/atendimento/os/consulta", headers=headers).status_code == 401


def test_jornada_do_cliente(client, cenario, token_cliente):
    """Autenticado por CPF, o cliente encontra a própria OS e consulta o detalhe."""
    headers = token_cliente(cenario["a"])

    minhas = client.get("/atendimento/os/consulta", headers=headers).json()
    assert [o["status"] for o in minhas] == ["ABERTA"]

    detalhe = client.get(f"/atendimento/os/{minhas[0]['id']}", headers=headers).json()
    assert detalhe["id"] == minhas[0]["id"]
