import pytest

from app.infrastructure.config import settings

TOKEN = "token-interno-de-teste"


@pytest.fixture
def token_interno(monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_INTERNO", TOKEN)
    return {"X-Token-Interno": TOKEN}


@pytest.fixture
def cliente(client, auth_headers):
    return client.post("/cadastro/clientes", headers=auth_headers, json={
        "nome": "Ana Souza", "cpf_cnpj": "529.982.247-25", "telefone": "11999990000",
    }).json()


def autenticar(client, cpf, headers):
    return client.post("/interno/clientes/autenticar", json={"cpf_cnpj": cpf}, headers=headers)


def test_devolve_o_cliente_pelo_cpf_com_ou_sem_mascara(client, cliente, token_interno):
    for cpf in ("52998224725", "529.982.247-25"):
        resposta = autenticar(client, cpf, token_interno)
        assert resposta.status_code == 200
        assert resposta.json() == {"id": cliente["id"], "nome": "Ana Souza", "cpf_cnpj": cliente["cpf_cnpj"], "ativo": True}


def test_cliente_inativo_e_devolvido_com_ativo_falso(client, auth_headers, cliente, token_interno):
    client.patch(f"/cadastro/clientes/{cliente['id']}", headers=auth_headers, json={"ativo": False})
    assert autenticar(client, "52998224725", token_interno).json()["ativo"] is False


def test_cpf_desconhecido_responde_404_e_cpf_invalido_422(client, token_interno):
    assert autenticar(client, "11144477735", token_interno).status_code == 404
    assert autenticar(client, "12345678900", token_interno).status_code == 422


@pytest.mark.parametrize("headers", [{}, {"X-Token-Interno": "errado"}])
def test_sem_o_token_interno_certo_responde_403(client, cliente, token_interno, headers):
    assert autenticar(client, "52998224725", headers).status_code == 403


def test_sem_token_configurado_a_rota_fica_fechada(client, cliente, monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_INTERNO", None)
    assert autenticar(client, "52998224725", {"X-Token-Interno": "qualquer"}).status_code == 403
