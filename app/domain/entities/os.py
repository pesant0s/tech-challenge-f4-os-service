import enum
from datetime import datetime, timezone
from decimal import Decimal

from app.domain.exceptions import BusinessRuleException


class StatusOS(str, enum.Enum):
    ABERTA = "ABERTA"
    AGUARDANDO_APROVACAO = "AGUARDANDO_APROVACAO"
    AGUARDANDO_PAGAMENTO = "AGUARDANDO_PAGAMENTO"
    NA_FILA = "NA_FILA"
    EM_DIAGNOSTICO = "EM_DIAGNOSTICO"
    EM_EXECUCAO = "EM_EXECUCAO"
    FINALIZADA = "FINALIZADA"
    ENTREGUE = "ENTREGUE"
    NEGADA = "NEGADA"
    CANCELADA = "CANCELADA"


class TipoItem(str, enum.Enum):
    SERVICO = "SERVICO"
    PECA = "PECA"


class ItemOS:
    """Item de uma Ordem de Serviço (serviço ou peça do catálogo); o preço chega com a precificação."""

    def __init__(self, tipo=None, item_id=None, quantidade=1, descricao=None,
                 preco_unitario=None, os_id=None, id=None):
        self.id = id
        self.os_id = os_id
        self.tipo = tipo
        self.item_id = item_id
        self.quantidade = quantidade
        self.descricao = descricao
        self.preco_unitario = preco_unitario

    def calcular_subtotal(self) -> Decimal:
        if self.preco_unitario is None:
            raise BusinessRuleException("Item ainda não precificado")
        return Decimal(str(self.preco_unitario)) * self.quantidade


class HistoricoStatusOS:
    """Entrada da OS em um status; a diferença entre entradas dá o tempo em cada status."""

    def __init__(self, status=None, entrou_em=None, os_id=None, id=None):
        self.id = id
        self.os_id = os_id
        self.status = status
        self.entrou_em = entrou_em


class OrdemDeServico:
    """Ordem de Serviço com máquina de estados de negócio. Entidade de domínio pura."""

    TRANSICOES_VALIDAS: dict[StatusOS, list[StatusOS]] = {
        StatusOS.ABERTA:               [StatusOS.AGUARDANDO_APROVACAO, StatusOS.CANCELADA],
        StatusOS.AGUARDANDO_APROVACAO: [StatusOS.AGUARDANDO_PAGAMENTO, StatusOS.NEGADA, StatusOS.CANCELADA],
        StatusOS.AGUARDANDO_PAGAMENTO: [StatusOS.NA_FILA, StatusOS.CANCELADA],
        StatusOS.NA_FILA:              [StatusOS.EM_DIAGNOSTICO],
        StatusOS.EM_DIAGNOSTICO:       [StatusOS.EM_EXECUCAO],
        StatusOS.EM_EXECUCAO:          [StatusOS.FINALIZADA],
        StatusOS.FINALIZADA:           [StatusOS.ENTREGUE],
        StatusOS.ENTREGUE:             [],
        StatusOS.NEGADA:               [],
        StatusOS.CANCELADA:            [],
    }

    CAMINHO_EXECUCAO = [StatusOS.NA_FILA, StatusOS.EM_DIAGNOSTICO, StatusOS.EM_EXECUCAO, StatusOS.FINALIZADA]

    def __init__(self, cliente_id=None, veiculo_id=None, status=StatusOS.ABERTA,
                 valor_total=None, criado_em=None, iniciado_em=None, finalizado_em=None,
                 itens=None, historico=None, id=None):
        self.id = id
        self.cliente_id = cliente_id
        self.veiculo_id = veiculo_id
        self.status = status
        self.valor_total = valor_total
        self.criado_em = criado_em
        self.iniciado_em = iniciado_em
        self.finalizado_em = finalizado_em
        # Ao carregar do banco o ORM não chama __init__, então as coleções não são sobrescritas.
        self.itens = itens if itens is not None else []
        self.historico = historico if historico is not None else [HistoricoStatusOS(status, datetime.now(timezone.utc))]

    def transicionar_para(self, novo_status: StatusOS) -> None:
        permitidos = self.TRANSICOES_VALIDAS.get(self.status, [])
        if novo_status not in permitidos:
            raise BusinessRuleException(
                f"Transição inválida: {self.status} → {novo_status}. "
                f"Permitidos: {[s.value for s in permitidos]}"
            )
        agora = datetime.now(timezone.utc)
        self.status = novo_status
        self.historico.append(HistoricoStatusOS(novo_status, agora))
        if novo_status == StatusOS.EM_EXECUCAO:
            self.iniciado_em = agora
        if novo_status == StatusOS.FINALIZADA:
            self.finalizado_em = agora

    def precificar(self, itens_precificados: list[dict], valor_total) -> None:
        """Aplica descrição e preço unitário calculados pelo orçamento e fixa o valor total."""
        for precificado in itens_precificados:
            item = next((i for i in self.itens
                         if i.tipo == precificado["tipo"] and str(i.item_id) == precificado["item_id"]), None)
            if item is None:
                raise BusinessRuleException(
                    f"Item {precificado['tipo']} {precificado['item_id']} não pertence à OS"
                )
            item.descricao = precificado["descricao"]
            item.preco_unitario = Decimal(precificado["preco_unitario"])
        self.valor_total = Decimal(valor_total)

    def avancar_execucao(self, alvo: StatusOS) -> None:
        """Percorre NA_FILA → EM_DIAGNOSTICO → EM_EXECUCAO → FINALIZADA até o alvo; repetir o evento não faz nada."""
        caminho = self.CAMINHO_EXECUCAO
        if self.status not in caminho or alvo not in caminho:
            raise BusinessRuleException(f"OS em {self.status} não está no caminho de execução")
        for proximo in caminho[caminho.index(self.status) + 1:caminho.index(alvo) + 1]:
            self.transicionar_para(proximo)
