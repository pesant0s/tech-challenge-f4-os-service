"""Rastreamento distribuído pela fila: o contexto do trace vai nos cabeçalhos AMQP (contrato da Saga)."""
from contextlib import contextmanager

import newrelic.agent


def cabecalhos_de_rastreamento() -> dict:
    """Cabeçalhos do trace atual; vazio sem o agente ativo ou fora de uma transação."""
    if not newrelic.agent.current_transaction():
        return {}
    cabecalhos = []
    newrelic.agent.insert_distributed_trace_headers(cabecalhos)
    return dict(cabecalhos)


@contextmanager
def consumo_rastreado(tipo: str, cabecalhos: dict):
    """Transação de background por mensagem, continuando o trace de quem a publicou."""
    with newrelic.agent.BackgroundTask(newrelic.agent.application(), name=tipo, group="Message/RabbitMQ"):
        newrelic.agent.accept_distributed_trace_headers(
            {k: str(v) for k, v in (cabecalhos or {}).items()}, transport_type="AMQP")
        yield
