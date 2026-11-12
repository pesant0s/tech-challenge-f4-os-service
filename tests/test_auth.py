def test_login_admin(client):
    r = client.post("/auth/token", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_login_credenciais_invalidas(client):
    r = client.post("/auth/token", data={"username": "admin", "password": "errada"})
    assert r.status_code == 401


def test_login_usuario_inexistente(client):
    r = client.post("/auth/token", data={"username": "naoexiste", "password": "qualquer"})
    assert r.status_code == 401


def test_admin_cria_usuario(client, auth_headers):
    r = client.post("/auth/usuarios", json={
        "username": "atendente1", "password": "atend123", "role": "ATENDENTE"
    }, headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["role"] == "ATENDENTE"
    assert r.json()["ativo"] is True


def test_admin_cria_outro_admin(client, auth_headers):
    r = client.post("/auth/usuarios", json={
        "username": "admin2", "password": "admin456", "role": "ADMIN"
    }, headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["role"] == "ADMIN"


def test_username_duplicado(client, auth_headers):
    client.post("/auth/usuarios", json={
        "username": "duplicado", "password": "pass123", "role": "ATENDENTE"
    }, headers=auth_headers)
    r = client.post("/auth/usuarios", json={
        "username": "duplicado", "password": "pass456", "role": "ATENDENTE"
    }, headers=auth_headers)
    assert r.status_code == 409


def test_atendente_nao_pode_criar_usuario(client, atendente_headers):
    r = client.post("/auth/usuarios", json={
        "username": "novo", "password": "pass123", "role": "ATENDENTE"
    }, headers=atendente_headers)
    assert r.status_code == 403


def test_admin_lista_usuarios(client, auth_headers):
    r = client.get("/auth/usuarios", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1  # admin existe


def test_atendente_nao_pode_listar_usuarios(client, atendente_headers):
    r = client.get("/auth/usuarios", headers=atendente_headers)
    assert r.status_code == 403


def test_desativar_usuario(client, auth_headers):
    u = client.post("/auth/usuarios", json={
        "username": "paradesativar", "password": "pass123", "role": "ATENDENTE"
    }, headers=auth_headers).json()
    r = client.delete(f"/auth/usuarios/{u['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["ativo"] is False


def test_usuario_desativado_nao_faz_login(client, auth_headers):
    u = client.post("/auth/usuarios", json={
        "username": "desativado", "password": "pass123", "role": "ATENDENTE"
    }, headers=auth_headers).json()
    client.delete(f"/auth/usuarios/{u['id']}", headers=auth_headers)
    r = client.post("/auth/token", data={"username": "desativado", "password": "pass123"})
    assert r.status_code == 401


def test_sem_token_retorna_401(client):
    r = client.get("/auth/usuarios")
    assert r.status_code == 401


def test_atendente_pode_criar_os(client, atendente_headers):
    """ATENDENTE tem acesso operacional: cria OS normalmente."""
    cliente = client.post("/cadastro/clientes", json={
        "nome": "Cliente Teste", "cpf_cnpj": "529.982.247-25", "telefone": "11999999999"
    }, headers=atendente_headers).json()
    veiculo = client.post("/cadastro/veiculos", json={
        "placa": "AAA-1111", "marca": "Fiat", "modelo": "Uno",
        "ano": "2020", "cliente_id": cliente["id"]
    }, headers=atendente_headers).json()
    r = client.post("/atendimento/os", json={
        "cliente_id": cliente["id"], "veiculo_id": veiculo["id"],
        "servicos": [{"servico_id": "00000000-0000-0000-0000-0000000000a1", "quantidade": 1}]
    }, headers=atendente_headers)
    assert r.status_code == 201


def test_atendente_nao_pode_ver_metricas(client, atendente_headers):
    r = client.get("/atendimento/os/metricas/tempo-medio", headers=atendente_headers)
    assert r.status_code == 403


def test_admin_pode_ver_metricas(client, auth_headers):
    r = client.get("/atendimento/os/metricas/tempo-medio", headers=auth_headers)
    assert r.status_code == 200
