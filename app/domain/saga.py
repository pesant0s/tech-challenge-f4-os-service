"""Saga orquestrada da abertura da OS (ADR-018). Domínio puro: decide o próximo comando, não publica."""
import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.entities.os import OrdemDeServico, StatusOS
from app.domain.exceptions import BusinessRuleException


class PassoSaga(str, enum.Enum):
    RESERVANDO_PECAS = "RESERVANDO_PECAS"
    GERANDO_ORCAMENTO = "GERANDO_ORCAMENTO"
    AGUARDANDO_APROVACAO = "AGUARDANDO_APROVACAO"
    AGUARDANDO_PAGAMENTO = "AGUARDANDO_PAGAMENTO"
    ENFILEIRANDO = "ENFILEIRANDO"
    EM_EXECUCAO = "EM_EXECUCAO"


class EstadoSaga(str, enum.Enum):
    EM_ANDAMENTO = "EM_ANDAMENTO"
    COMPENSANDO = "COMPENSANDO"
    CONCLUIDA = "CONCLUIDA"
    COMPENSADA = "COMPENSADA"
    FALHA_NA_COMPENSACAO = "FALHA_NA_COMPENSACAO"


# Chave de roteamento de cada comando, igual ao contrato (docs/contratos no tech-challenge-f4-plataforma).
ROTAS_DOS_COMANDOS = {
    "ReservarPecas": "execucao.reservar-pecas",
    "LiberarReserva": "execucao.liberar-reserva",
    "EnfileirarOS": "execucao.enfileirar-os",
    "GerarOrcamento": "billing.gerar-orcamento",
    "Cobrar": "billing.cobrar",
    "CancelarOrcamento": "billing.cancelar-orcamento",
    "Reembolsar": "billing.reembolsar",
}

# Evento que confirma cada compensação, e o efeito que ela desfaz.
CONFIRMACOES = {
    "ReservaLiberada": ("LiberarReserva", "reserva_feita"),
    "OrcamentoCancelado": ("CancelarOrcamento", "orcamento_ativo"),
    "PagamentoReembolsado": ("Reembolsar", "pagamento_confirmado"),
}

# Status da OS a que cada evento da Execução leva.
AVANCOS_DA_EXECUCAO = {
    "DiagnosticoIniciado": StatusOS.EM_DIAGNOSTICO,
    "ExecucaoIniciada": StatusOS.EM_EXECUCAO,
    "ExecucaoFinalizada": StatusOS.FINALIZADA,
}

PASSOS_CANCELAVEIS = (
    PassoSaga.RESERVANDO_PECAS, PassoSaga.GERANDO_ORCAMENTO,
    PassoSaga.AGUARDANDO_APROVACAO, PassoSaga.AGUARDANDO_PAGAMENTO,
)
TENTATIVAS_DE_ENFILEIRAMENTO = 3


@dataclass(frozen=True)
class Comando:
    tipo: str
    payload: dict = field(default_factory=dict)

    @property
    def rota(self) -> str:
        return ROTAS_DOS_COMANDOS[self.tipo]


@dataclass(frozen=True)
class PrazosSaga:
    resposta: timedelta = timedelta(minutes=5)
    aprovacao: timedelta = timedelta(hours=24)
    folga: timedelta = timedelta(minutes=5)


class SagaAberturaOS:
    """Estado de uma Saga e as regras de transição; cada método devolve os comandos a enviar."""

    def __init__(self, os_id=None, passo=PassoSaga.RESERVANDO_PECAS, estado=EstadoSaga.EM_ANDAMENTO,
                 motivo=None, compensacoes=None, reserva_feita=False, orcamento_id=None, orcamento_ativo=False,
                 pagamento_id=None, pagamento_confirmado=False, tentativas=0, expira_em=None, id=None):
        self.id = id or uuid.uuid4()
        self.os_id = os_id
        self.passo = passo
        self.estado = estado
        self.motivo = motivo
        self.compensacoes = list(compensacoes or [])
        self.reserva_feita = reserva_feita
        self.orcamento_id = orcamento_id
        self.orcamento_ativo = orcamento_ativo
        self.pagamento_id = pagamento_id
        self.pagamento_confirmado = pagamento_confirmado
        self.tentativas = tentativas
        self.expira_em = expira_em

    @classmethod
    def abrir(cls, os: OrdemDeServico, agora: datetime, prazos: PrazosSaga) -> tuple["SagaAberturaOS", list[Comando]]:
        saga = cls(os_id=os.id, expira_em=agora + prazos.resposta)
        itens = [{"tipo": i.tipo.value, "item_id": str(i.item_id), "quantidade": i.quantidade} for i in os.itens]
        return saga, [Comando("ReservarPecas", {"itens": itens})]

    def ao_receber(self, tipo: str, payload: dict, os: OrdemDeServico, cliente: dict,
                   agora: datetime, prazos: PrazosSaga) -> list[Comando] | None:
        """Aplica um evento; None quando ele não corresponde ao momento da Saga e deve ser descartado."""
        if tipo == "CobrancaCriada":
            return self._registrar_cobranca(payload, agora, prazos)
        if tipo == "PagamentoConfirmado" and self.estado in (EstadoSaga.COMPENSANDO, EstadoSaga.COMPENSADA):
            return self._reembolsar_pagamento_tardio(payload, agora, prazos)
        if self.estado == EstadoSaga.COMPENSANDO:
            return self._ao_receber_compensando(tipo, os, agora, prazos)
        if tipo in AVANCOS_DA_EXECUCAO and self.passo == PassoSaga.EM_EXECUCAO:
            os.avancar_execucao(AVANCOS_DA_EXECUCAO[tipo])
            if tipo == "ExecucaoFinalizada":
                self.estado = EstadoSaga.CONCLUIDA
            return []
        if self.estado != EstadoSaga.EM_ANDAMENTO:
            return None
        tratar = self._TRATADORES.get((self.passo, tipo))
        return tratar(self, payload, os, cliente, agora, prazos) if tratar else None

    def ao_expirar(self, os: OrdemDeServico, agora: datetime, prazos: PrazosSaga) -> list[Comando]:
        """Prazo do passo esgotado: depois do pagamento tenta seguir em frente; antes dele, desfaz."""
        if self.estado == EstadoSaga.COMPENSANDO:
            self.expira_em = agora + prazos.resposta
            return [self._comando_de_compensacao()]
        if self.estado != EstadoSaga.EM_ANDAMENTO or self.passo == PassoSaga.EM_EXECUCAO:
            self.expira_em = None
            return []
        if self.passo == PassoSaga.ENFILEIRANDO and self.tentativas < TENTATIVAS_DE_ENFILEIRAMENTO:
            self.tentativas += 1
            self.expira_em = agora + prazos.resposta
            return [Comando("EnfileirarOS", {"veiculo_id": str(os.veiculo_id)})]
        self._supor_efeito_do_passo_em_curso()
        return self._compensar("PRAZO_ESGOTADO", os, agora, prazos)

    def cancelar_pela_oficina(self, os: OrdemDeServico, agora: datetime, prazos: PrazosSaga) -> list[Comando]:
        if self.estado != EstadoSaga.EM_ANDAMENTO or self.passo not in PASSOS_CANCELAVEIS:
            raise BusinessRuleException(f"A OS não pode mais ser cancelada (passo {self.passo.value}, {self.estado.value})")
        self._supor_efeito_do_passo_em_curso()
        return self._compensar("CANCELADA_PELA_OFICINA", os, agora, prazos)

    # ─── Passos ──────────────────────────────────────────────────────────────

    def _pecas_reservadas(self, payload, os, cliente, agora, prazos):
        self.reserva_feita = True
        os.precificar(payload["itens"], payload["valor_total"])
        return self._avancar(PassoSaga.GERANDO_ORCAMENTO, agora + prazos.resposta, Comando("GerarOrcamento", {
            "cliente": cliente, "itens": payload["itens"], "valor_total": payload["valor_total"],
        }))

    def _reserva_recusada(self, payload, os, cliente, agora, prazos):
        return self._compensar("RESERVA_RECUSADA", os, agora, prazos)

    def _orcamento_gerado(self, payload, os, cliente, agora, prazos):
        self.orcamento_id = payload["orcamento_id"]
        self.orcamento_ativo = True
        os.transicionar_para(StatusOS.AGUARDANDO_APROVACAO)
        expira = datetime.fromisoformat(payload["expira_em"]) + prazos.folga
        return self._avancar(PassoSaga.AGUARDANDO_APROVACAO, min(expira, agora + prazos.aprovacao))

    def _orcamento_aprovado(self, payload, os, cliente, agora, prazos):
        os.transicionar_para(StatusOS.AGUARDANDO_PAGAMENTO)
        return self._avancar(PassoSaga.AGUARDANDO_PAGAMENTO, agora + prazos.resposta,
                             Comando("Cobrar", {"orcamento_id": self.orcamento_id}))

    def _orcamento_rejeitado(self, payload, os, cliente, agora, prazos):
        self.orcamento_ativo = False  # o Billing já encerrou o orçamento
        return self._compensar(payload["motivo"], os, agora, prazos)

    def _pagamento_confirmado(self, payload, os, cliente, agora, prazos):
        self.pagamento_confirmado = True
        self.pagamento_id = payload["pagamento_id"]
        return self._avancar(PassoSaga.ENFILEIRANDO, agora + prazos.resposta,
                             Comando("EnfileirarOS", {"veiculo_id": str(os.veiculo_id)}))

    def _pagamento_recusado(self, payload, os, cliente, agora, prazos):
        return self._compensar(payload["motivo"], os, agora, prazos)

    def _os_enfileirada(self, payload, os, cliente, agora, prazos):
        self.reserva_feita = False  # a Execução consumiu a reserva ao enfileirar
        os.transicionar_para(StatusOS.NA_FILA)
        return self._avancar(PassoSaga.EM_EXECUCAO, None)

    def _enfileiramento_recusado(self, payload, os, cliente, agora, prazos):
        return self._compensar("ENFILEIRAMENTO_RECUSADO", os, agora, prazos)

    _TRATADORES = {
        (PassoSaga.RESERVANDO_PECAS, "PecasReservadas"): _pecas_reservadas,
        (PassoSaga.RESERVANDO_PECAS, "ReservaRecusada"): _reserva_recusada,
        (PassoSaga.GERANDO_ORCAMENTO, "OrcamentoGerado"): _orcamento_gerado,
        (PassoSaga.AGUARDANDO_APROVACAO, "OrcamentoAprovado"): _orcamento_aprovado,
        (PassoSaga.AGUARDANDO_APROVACAO, "OrcamentoRejeitado"): _orcamento_rejeitado,
        (PassoSaga.AGUARDANDO_PAGAMENTO, "PagamentoConfirmado"): _pagamento_confirmado,
        (PassoSaga.AGUARDANDO_PAGAMENTO, "PagamentoRecusado"): _pagamento_recusado,
        (PassoSaga.ENFILEIRANDO, "OSEnfileirada"): _os_enfileirada,
        (PassoSaga.ENFILEIRANDO, "EnfileiramentoRecusado"): _enfileiramento_recusado,
    }

    # ─── Compensação ─────────────────────────────────────────────────────────

    def _avancar(self, passo, expira_em, *comandos):
        self.passo = passo
        self.expira_em = expira_em
        self.tentativas = 0
        return list(comandos)

    def _compensar(self, motivo, os, agora, prazos):
        """Desfaz do passo mais recente para o mais antigo, uma compensação por vez."""
        self.estado = EstadoSaga.COMPENSANDO
        self.motivo = motivo
        self.compensacoes = [nome for nome, pendente in (
            ("Reembolsar", self.pagamento_confirmado),
            ("CancelarOrcamento", self.orcamento_ativo),
            ("LiberarReserva", self.reserva_feita),
        ) if pendente]
        return self._proxima_compensacao(os, agora, prazos)

    def _proxima_compensacao(self, os, agora, prazos):
        if not self.compensacoes:
            self.estado = EstadoSaga.COMPENSADA
            self.expira_em = None
            if os.status not in (StatusOS.NEGADA, StatusOS.CANCELADA):
                os.transicionar_para(StatusOS.NEGADA if self.motivo == "ORCAMENTO_REJEITADO" else StatusOS.CANCELADA)
            return []
        self.expira_em = agora + prazos.resposta
        return [self._comando_de_compensacao()]

    def _comando_de_compensacao(self):
        return Comando(self.compensacoes[0], {"motivo": self.motivo})

    def _ao_receber_compensando(self, tipo, os, agora, prazos):
        if tipo == "ReembolsoFalhou":
            self.estado = EstadoSaga.FALHA_NA_COMPENSACAO
            self.expira_em = None
            return []
        compensacao, efeito = CONFIRMACOES.get(tipo, (None, None))
        if not self.compensacoes or self.compensacoes[0] != compensacao:
            return None
        self.compensacoes.pop(0)
        setattr(self, efeito, False)
        return self._proxima_compensacao(os, agora, prazos)

    def _reembolsar_pagamento_tardio(self, payload, agora, prazos):
        """Pagamento que chega depois da decisão de desfazer a Saga também é devolvido."""
        self.pagamento_id = payload["pagamento_id"]
        if self.pagamento_confirmado:
            return None
        self.pagamento_confirmado = True
        self.compensacoes.append("Reembolsar")
        if self.estado == EstadoSaga.COMPENSADA:
            self.estado = EstadoSaga.COMPENSANDO
            self.expira_em = agora + prazos.resposta
            return [self._comando_de_compensacao()]
        return []

    def _registrar_cobranca(self, payload, agora, prazos):
        self.pagamento_id = payload["pagamento_id"]
        if self.estado == EstadoSaga.EM_ANDAMENTO and self.passo == PassoSaga.AGUARDANDO_PAGAMENTO:
            self.expira_em = datetime.fromisoformat(payload["expira_em"]) + prazos.folga
        return []

    def _supor_efeito_do_passo_em_curso(self):
        # Sem resposta, o comando pode ter sido executado: a compensação é idempotente, então desfaz por garantia.
        if self.passo == PassoSaga.RESERVANDO_PECAS:
            self.reserva_feita = True
        elif self.passo == PassoSaga.GERANDO_ORCAMENTO:
            self.orcamento_ativo = True
