from collections import defaultdict
from sqlalchemy.orm import Session
from app.domain.entities.os import HistoricoStatusOS, OrdemDeServico, StatusOS
from app.domain.exceptions import NotFoundException


class OSRepositoryAdapter:
    """Implementação SQLAlchemy do OSRepositoryPort."""

    def __init__(self, db: Session):
        self._db = db

    def buscar_por_id(self, os_id) -> OrdemDeServico | None:
        return self._db.query(OrdemDeServico).filter(OrdemDeServico.id == os_id).first()

    def buscar_para_escrita(self, os_id) -> OrdemDeServico:
        """Busca com SELECT FOR UPDATE — usar em use cases que alteram estado."""
        os = self._db.query(OrdemDeServico).filter(OrdemDeServico.id == os_id).with_for_update().first()
        if not os:
            raise NotFoundException("Ordem de Serviço não encontrada")
        return os

    def listar(self, skip: int = 0, limit: int = 100, status: StatusOS | None = None) -> list[OrdemDeServico]:
        q = self._db.query(OrdemDeServico)
        if status:
            q = q.filter(OrdemDeServico.status == status)
        return q.order_by(OrdemDeServico.criado_em.desc()).offset(skip).limit(limit).all()

    def buscar_por_cliente_id(self, cliente_id) -> list[OrdemDeServico]:
        return (
            self._db.query(OrdemDeServico)
            .filter(OrdemDeServico.cliente_id == cliente_id)
            .order_by(OrdemDeServico.criado_em.desc())
            .all()
        )

    def adicionar(self, entidade) -> None:
        self._db.add(entidade)

    def flush(self) -> None:
        self._db.flush()

    def commit(self) -> None:
        self._db.commit()

    def rollback(self) -> None:
        self._db.rollback()

    def refresh(self, entidade) -> None:
        self._db.refresh(entidade)

    def tempo_medio_execucao(self) -> dict:
        historico = self._db.query(HistoricoStatusOS).order_by(HistoricoStatusOS.os_id, HistoricoStatusOS.entrou_em).all()
        duracoes = defaultdict(list)
        for atual, proximo in zip(historico, historico[1:]):
            if atual.os_id == proximo.os_id:
                duracoes[atual.status.value].append((proximo.entrou_em - atual.entrou_em).total_seconds())
        os_list = self._db.query(OrdemDeServico).filter(
            OrdemDeServico.iniciado_em.isnot(None),
            OrdemDeServico.finalizado_em.isnot(None),
        ).all()
        total = sum((o.finalizado_em - o.iniciado_em).total_seconds() for o in os_list)
        return {
            "tempo_medio_minutos": round(total / len(os_list) / 60, 2) if os_list else 0,
            "total_os_finalizadas": len(os_list),
            "por_status": {s: round(sum(d) / len(d) / 60, 2) for s, d in duracoes.items()},
        }
