import logging

logger = logging.getLogger("oficina.os")


def registrar_mudanca_de_status(os, indice: int = -1) -> None:
    """Loga uma entrada do histórico (a última, por padrão) e o tempo no status anterior; base dos dashboards."""
    indice %= len(os.historico)
    atual = os.historico[indice]
    anterior = os.historico[indice - 1] if indice else None
    logger.info("OS %s entrou em %s", os.id, atual.status.value, extra={
        "evento": "os_status",
        "os_id": str(os.id),
        "status_anterior": anterior.status.value if anterior else None,
        "status_novo": atual.status.value,
        "segundos_no_status_anterior": (atual.entrou_em - anterior.entrou_em).total_seconds() if anterior else None,
    })
