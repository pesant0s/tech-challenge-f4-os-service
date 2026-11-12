"""Restrições CHECK: o banco recusa o que a API já não deixaria passar."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

OS = "'00000000-0000-0000-0000-0000000000a1'"
ITEM = "'00000000-0000-0000-0000-0000000000b1'"


@pytest.mark.parametrize("quantidade,preco", [(0, "NULL"), (-1, "10"), (1, "-0.01")],
                         ids=["quantidade zero", "quantidade negativa", "preço negativo"])
def test_banco_recusa_item_inconsistente(db, quantidade, preco):
    sql = (f"INSERT INTO itens_os (id, os_id, tipo, item_id, quantidade, preco_unitario) "
           f"VALUES ('i1', {OS}, 'PECA', {ITEM}, {quantidade}, {preco})")
    with pytest.raises(IntegrityError):
        db.execute(text(sql))


def test_banco_aceita_item_sem_preco(db):
    db.execute(text(f"INSERT INTO itens_os (id, os_id, tipo, item_id, quantidade) "
                    f"VALUES ('i1', {OS}, 'SERVICO', {ITEM}, 1)"))
