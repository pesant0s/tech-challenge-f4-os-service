from app.application.eventos_os import registrar_mudanca_de_status
from app.domain.entities.os import OrdemDeServico, ItemOS, TipoItem
from app.domain.exceptions import NotFoundException, BusinessRuleException
from app.domain.ports.os_repository import OSRepositoryPort


class CriarOSUseCase:
    """Abre a OS em ABERTA com os itens pedidos; preço e total chegam depois, na precificação."""

    def __init__(self, os_repo: OSRepositoryPort, cliente_repo):
        self._os_repo = os_repo
        self._cliente_repo = cliente_repo

    def executar(self, cliente_id, veiculo_id, servicos, pecas) -> OrdemDeServico:
        cliente = self._cliente_repo.buscar_por_id(cliente_id)
        if not cliente:
            raise NotFoundException("Cliente não encontrado")
        if not cliente.ativo:
            raise BusinessRuleException("Cliente inativo")
        if not any(v.id == veiculo_id for v in cliente.veiculos):
            raise BusinessRuleException("Veículo não encontrado para este cliente")

        itens = [ItemOS(tipo=TipoItem.SERVICO, item_id=s.servico_id, quantidade=s.quantidade) for s in servicos]
        itens += [ItemOS(tipo=TipoItem.PECA, item_id=p.peca_id, quantidade=p.quantidade) for p in pecas]

        os = OrdemDeServico(cliente_id=cliente_id, veiculo_id=veiculo_id, itens=itens)
        self._os_repo.adicionar(os)
        self._os_repo.commit()
        self._os_repo.refresh(os)
        registrar_mudanca_de_status(os)
        return os
