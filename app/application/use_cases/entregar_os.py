from app.application.eventos_os import registrar_mudanca_de_status
from app.domain.entities.os import OrdemDeServico, StatusOS
from app.domain.ports.os_repository import OSRepositoryPort


class EntregarOSUseCase:
    """Registra a entrega do veículo ao cliente: FINALIZADA → ENTREGUE."""

    def __init__(self, os_repo: OSRepositoryPort):
        self._os_repo = os_repo

    def executar(self, os_id) -> OrdemDeServico:
        os = self._os_repo.buscar_para_escrita(os_id)
        os.transicionar_para(StatusOS.ENTREGUE)
        self._os_repo.commit()
        self._os_repo.refresh(os)
        registrar_mudanca_de_status(os)
        return os
