"""Envelope das mensagens da Saga, igual ao contrato (docs/contratos no tech-challenge-f4-plataforma)."""
import uuid
from datetime import datetime

ORIGEM = "os-service"
EXCHANGE_COMANDOS = "oficina.comandos"


def montar_envelope(tipo: str, saga_id, os_id, payload: dict, agora: datetime) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "tipo": tipo,
        "versao": 1,
        "saga_id": str(saga_id),
        "os_id": str(os_id),
        "ocorrido_em": agora.isoformat().replace("+00:00", "Z"),
        "origem": ORIGEM,
        "payload": payload,
    }
