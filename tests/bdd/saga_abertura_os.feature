# language: pt
Funcionalidade: Saga da abertura da ordem de serviço
  Para manter a OS consistente entre OS Service, Execução e Billing
  Como oficina
  Quero que a abertura da OS siga a Saga orquestrada e desfaça os passos anteriores quando um falhar

  Contexto:
    Dado um cliente com um veículo cadastrado
    E o atendente abre uma OS com 1 serviço e 2 unidades de uma peça

  Cenário: Fluxo completo da OS, da abertura à entrega
    Então a Execução recebe o comando "ReservarPecas"
    Quando a Execução responde "PecasReservadas" com valor total de "150.00"
    Então o Billing recebe o comando "GerarOrcamento"
    Quando o Billing responde "OrcamentoGerado"
    Então a OS fica "AGUARDANDO_APROVACAO"
    Quando o Billing responde "OrcamentoAprovado"
    Então o Billing recebe o comando "Cobrar"
    E a OS fica "AGUARDANDO_PAGAMENTO"
    Quando o Billing responde "CobrancaCriada"
    E o Billing responde "PagamentoConfirmado"
    Então a Execução recebe o comando "EnfileirarOS"
    Quando a Execução responde "OSEnfileirada"
    Então a OS fica "NA_FILA"
    Quando a Execução responde "DiagnosticoIniciado"
    E a Execução responde "ExecucaoIniciada"
    E a Execução responde "ExecucaoFinalizada"
    Então a OS fica "FINALIZADA"
    E a Saga termina "CONCLUIDA"
    Quando o atendente entrega o veículo
    Então a OS fica "ENTREGUE"

  Esquema do Cenário: Falha num passo desfaz os anteriores, do mais recente para o mais antigo
    Dado que a Saga chegou até "<ultimo_sucesso>"
    Quando chega a falha "<falha>"
    Então as compensações saem nesta ordem: "<compensacoes>"
    E a OS termina "<status_final>" com a Saga "COMPENSADA"

    Exemplos:
      | ultimo_sucesso      | falha                  | compensacoes                                  | status_final |
      | ReservarPecas       | ReservaRecusada        | nenhuma                                       | CANCELADA    |
      | OrcamentoGerado     | OrcamentoRejeitado     | LiberarReserva                                | NEGADA       |
      | CobrancaCriada      | PagamentoRecusado      | CancelarOrcamento, LiberarReserva             | CANCELADA    |
      | PagamentoConfirmado | EnfileiramentoRecusado | Reembolsar, CancelarOrcamento, LiberarReserva | CANCELADA    |

  Cenário: Mensagem repetida pelo broker não repete o efeito
    Quando a Execução responde "PecasReservadas" com valor total de "150.00"
    E o broker entrega de novo a última mensagem
    Então o Billing recebe o comando "GerarOrcamento" uma única vez
