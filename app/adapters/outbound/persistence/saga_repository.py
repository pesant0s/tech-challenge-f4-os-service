import uuid

from sqlalchemy.orm import Session

from app.domain.exceptions import NotFoundException
from app.domain.saga import EstadoSaga, SagaAberturaOS


class SagaRepositoryAdapter:
    def __init__(self, db: Session):
        self._db = db

    def adicionar(self, saga: SagaAberturaOS) -> None:
        self._db.add(saga)

    def buscar_por_os(self, os_id) -> SagaAberturaOS | None:
        return self._db.query(SagaAberturaOS).filter(SagaAberturaOS.os_id == os_id).first()

    def buscar_para_escrita(self, saga_id) -> SagaAberturaOS:
        saga = self._db.query(SagaAberturaOS).filter(SagaAberturaOS.id == uuid.UUID(str(saga_id))).with_for_update().first()
        if not saga:
            raise NotFoundException(f"Saga {saga_id} não encontrada")
        return saga

    def vencidas_para_escrita(self, agora, limite: int) -> list[SagaAberturaOS]:
        """Sagas com prazo esgotado; SKIP LOCKED deixa cada réplica com um lote diferente."""
        return (
            self._db.query(SagaAberturaOS)
            .filter(SagaAberturaOS.expira_em <= agora,
                    SagaAberturaOS.estado.in_([EstadoSaga.EM_ANDAMENTO, EstadoSaga.COMPENSANDO]))
            .order_by(SagaAberturaOS.expira_em)
            .limit(limite)
            .with_for_update(skip_locked=True)
            .all()
        )
