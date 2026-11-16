# tech-challenge-f4-os-service

**OS Service**: serviço de atendimento da oficina mecânica. Cuida de clientes, veículos, usuários, login de
funcionários, ordens de serviço (OS) e histórico de status, e é o **orquestrador da Saga** que conduz cada OS
pela Execução e pelo Billing, do orçamento à entrega.

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=pesant0s_tech-challenge-f4-os-service&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=pesant0s_tech-challenge-f4-os-service)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=pesant0s_tech-challenge-f4-os-service&metric=coverage)](https://sonarcloud.io/summary/new_code?id=pesant0s_tech-challenge-f4-os-service)

> Pós-Tech Software Architecture · FIAP · Tech Challenge Fase 04

| | |
|---|---|
| **Papel** | Atendimento (clientes, veículos, usuários, OS) e orquestrador da Saga de abertura da OS |
| **Runtime** | Amazon EKS · namespace `os-service` · Deployment com 2 réplicas e HPA de 2 a 4 |
| **Exposição** | API Gateway → VPC Link → NLB interno (porta 80) → NodePort 30080 |
| **Banco** | PostgreSQL 16 próprio (Amazon RDS `tech-challenge-f4-os-service-db`), migrations com Alembic |
| **Mensageria** | RabbitMQ: publica comandos em `oficina.comandos`, consome eventos da fila `os-service.eventos` |
| **Autenticação** | JWT HS256: emite o token de funcionário e valida o de cliente, emitido pela Lambda de CPF |
| **Observabilidade** | New Relic (APM, logs JSON com `saga_id` e `os_id`, traces pela fila, dashboard e alertas) |
| **Stack** | Python 3.12 · FastAPI 0.111 · SQLAlchemy 2.0 (mapeamento imperativo) · Alembic · pika 1.3 · pytest e pytest-bdd · Docker · Kustomize · Terraform · GitHub Actions |

## Os cinco repositórios

A solução é dividida em cinco repositórios independentes, cada um com código, pipeline e infraestrutura
próprios, na organização [`pesant0s`](https://github.com/pesant0s) do GitHub.

| Repositório | Papel |
|---|---|
| `tech-challenge-f4-plataforma` | VPC, cluster EKS, NLB, API Gateway, RabbitMQ no cluster, parâmetros do SSM, New Relic (dashboard e alertas), contrato AsyncAPI das mensagens, ADR-017 a ADR-024, coleção Postman e ambiente local da Saga |
| **`tech-challenge-f4-os-service`** (este) | Clientes, veículos, usuários e login, OS e histórico de status; orquestrador da Saga; PostgreSQL |
| `tech-challenge-f4-execucao-service` | Catálogo de serviços e peças, estoque (reserva, baixa e liberação), fila de execução, diagnóstico e reparo; PostgreSQL |
| `tech-challenge-f4-billing-service` | Orçamento, aprovação do cliente, cobrança por QR Code dinâmico e reembolso no Mercado Pago; MongoDB |
| `tech-challenge-f4-auth-lambda` | Lambda `POST /auth/cpf`: valida o CPF, consulta o cliente no OS Service e emite o JWT de cliente |

## Sumário

1. [Visão geral do sistema](#1-visão-geral-do-sistema)
2. [Arquitetura do serviço](#2-arquitetura-do-serviço)
3. [Responsabilidades e limites](#3-responsabilidades-e-limites)
4. [Máquina de estados da OS](#4-máquina-de-estados-da-os)
5. [A Saga](#5-a-saga)
6. [Mensageria](#6-mensageria)
7. [Modelo de dados](#7-modelo-de-dados)
8. [API REST](#8-api-rest)
9. [Configuração](#9-configuração)
10. [Como rodar localmente](#10-como-rodar-localmente)
11. [Testes e cobertura](#11-testes-e-cobertura)
12. [Pipeline CI/CD](#12-pipeline-cicd)
13. [Infraestrutura própria (`infra/`)](#13-infraestrutura-própria-infra)
14. [Kubernetes (`k8s/`)](#14-kubernetes-k8s)
15. [Deploy na AWS](#15-deploy-na-aws)
16. [Observabilidade](#16-observabilidade)
17. [Decisões arquiteturais](#17-decisões-arquiteturais)
18. [Limitações conhecidas](#18-limitações-conhecidas)
19. [Estrutura do repositório](#19-estrutura-do-repositório)

---

## 1. Visão geral do sistema

Funcionários e clientes entram pelo API Gateway, que roteia por prefixo de caminho para o serviço dono da
rota. Os três serviços não se chamam por HTTP: trocam comandos e eventos pelo RabbitMQ. A única chamada
síncrona entre componentes da solução é a da Lambda de CPF ao OS Service, pela rota interna `/interno/clientes/autenticar`,
que não tem rota no API Gateway.

```mermaid
flowchart LR
  U["Funcionário / Cliente"]
  MP["Mercado Pago<br/>Orders API · QR dinâmico"]
  NR["New Relic<br/>APM · logs · traces · dashboards"]
  GH["GitHub Actions<br/>testes · SonarQube · deploy"]
  subgraph AWS["AWS · us-east-1"]
    APIGW["API Gateway HTTP<br/>rota por prefixo"]
    L["Lambda tech-challenge-f4-auth<br/>POST /auth/cpf"]
    ECR["ECR · um por serviço"]
    SSM["SSM Parameter Store<br/>/tech-challenge-f4/*"]
    subgraph VPC["VPC"]
      NLB["NLB interno<br/>80 · 8081 · 8082"]
      subgraph EKS["EKS · 3 nós t3.medium"]
        OS["OS Service<br/>orquestrador da Saga"]
        EX["Execução e Produção"]
        BI["Billing"]
        RMQ["RabbitMQ<br/>namespace mensageria"]
        MDB[("MongoDB rs0<br/>namespace billing-service")]
      end
      RDS1[("RDS PostgreSQL<br/>os-service")]
      RDS2[("RDS PostgreSQL<br/>execucao-service")]
    end
  end
  U --> APIGW
  APIGW -->|"POST /auth/cpf"| L
  APIGW -->|"VPC Link"| NLB
  L -->|"/interno/clientes/autenticar"| NLB
  NLB -->|"NodePort 30080"| OS
  NLB -->|"NodePort 30081"| EX
  NLB -->|"NodePort 30082"| BI
  OS --- RDS1
  EX --- RDS2
  BI --- MDB
  OS <-->|"comandos e eventos"| RMQ
  EX <--> RMQ
  BI <--> RMQ
  BI -->|"cobrança · consulta · cancelamento · reembolso"| MP
  MP -.->|"webhook /billing/webhooks/mercadopago"| APIGW
  EKS -.->|"agente"| NR
  GH -->|"imagem"| ECR
  GH -->|"deploy"| EKS
```

O que passa por este serviço:

- **Entrada HTTP.** O API Gateway encaminha `ANY /auth/{proxy+}` (menos `POST /auth/cpf`, que vai para a
  Lambda), `ANY /cadastro/{proxy+}`, `ANY /atendimento/{proxy+}`, `GET /docs`, `GET /openapi.json`,
  `GET /redoc` e `GET /health` para o NLB, na porta 80, e dali para o NodePort 30080. Os caminhos chegam
  ao serviço sem reescrita.
- **Rota interna.** `/interno/*` só é alcançável de dentro da VPC: a Lambda chama
  `http://<dns do NLB>:80/interno/clientes/autenticar`.
- **Mensageria.** O serviço publica comandos para a Execução e o Billing e consome os eventos deles.
- **Banco.** Um RDS PostgreSQL só dele, em subnets privadas, aberto apenas para o security group do cluster.

A visão completa do sistema, o roteiro de subida do ambiente e o ambiente local da Saga estão no README do
repositório `tech-challenge-f4-plataforma`.

### Documentação relacionada

| Documento | Onde está |
|---|---|
| Visão geral, ambiente local da Saga e roteiro de subida | `tech-challenge-f4-plataforma` · `README.md` |
| Contrato das mensagens (AsyncAPI 3.0) | `tech-challenge-f4-plataforma` · `docs/contratos/asyncapi.yaml` e `docs/contratos/README.md` |
| ADR-017 a ADR-024 | `tech-challenge-f4-plataforma` · `docs/adr/` |
| Contrato de infraestrutura (SSM, portas, rotas, segredos) | `tech-challenge-f4-plataforma` · `docs/infraestrutura/contrato.md` |
| Coleção Postman da Saga | `tech-challenge-f4-plataforma` · `docs/postman/oficina-fase04.postman_collection.json` e `local.postman_environment.json` |
| Modelo de dados | este repositório · [`docs/modelo-de-dados.md`](docs/modelo-de-dados.md) |
| Swagger | `http://localhost:8000/docs` no docker compose · `<url_api>/docs` na AWS |

---

## 2. Arquitetura do serviço

Arquitetura hexagonal (Ports and Adapters). A regra de dependência aponta para dentro: adapters dependem de
casos de uso, e casos de uso dependem do domínio. O domínio não importa SQLAlchemy, FastAPI nem pika: as
entidades, a Saga e as exceções só usam a biblioteca padrão (o único import de framework no domínio é o
`BeforeValidator` do Pydantic, no value object de documentos). O mapeamento objeto-relacional é imperativo
(`registry.map_imperatively`, em `app/infrastructure/orm_mapping.py`), então as entidades não sabem que existe
um banco.

```mermaid
flowchart TB
  FUNC["Funcionário · Cliente · Lambda de CPF"]
  RMQ["RabbitMQ"]
  PG[("PostgreSQL")]

  subgraph IN["Adapters inbound"]
    HTTP["HTTP · FastAPI<br/>atendimento · cadastro · auth · interno"]
    CONS["Consumo de eventos<br/>tratar_mensagem"]
  end

  subgraph APP["Application"]
    UCOS["CriarOS · EntregarOS<br/>ObterOS · ListarOS"]
    UCCAD["GerenciarClientes<br/>GerenciarVeiculos"]
    UCSAGA["ProcessarEventoSaga<br/>VerificarPrazosSaga · CancelarOS"]
  end

  subgraph DOM["Domain"]
    ENT["OrdemDeServico · ItemOS · HistoricoStatusOS<br/>Cliente · Veiculo · Usuario · CpfCnpj · Placa"]
    SAGA["SagaAberturaOS<br/>PassoSaga · EstadoSaga · PrazosSaga"]
  end

  subgraph OUT["Adapters outbound"]
    REPO["Repositórios SQLAlchemy<br/>OS · Cliente · Auth · Saga"]
    BOX["Outbox e inbox"]
    PUB["Relay do outbox<br/>publicar_pendentes"]
  end

  subgraph INFRA["Infrastructure"]
    TRAB["TrabalhadorMensageria<br/>thread de cada processo"]
    CFG["config · database · orm_mapping<br/>security · logging_config · rastreamento"]
  end

  FUNC --> HTTP
  RMQ --> CONS
  HTTP --> UCOS
  HTTP --> UCCAD
  HTTP --> UCSAGA
  CONS --> UCSAGA
  TRAB --> CONS
  TRAB --> PUB
  TRAB --> UCSAGA
  UCOS --> ENT
  UCCAD --> ENT
  UCSAGA --> SAGA
  UCSAGA --> ENT
  UCOS --> REPO
  UCCAD --> REPO
  UCSAGA --> REPO
  UCOS --> BOX
  UCSAGA --> BOX
  REPO --> PG
  BOX --> PG
  PUB --> BOX
  PUB --> RMQ
```

### Pacotes de `app/`

| Pacote | Conteúdo |
|---|---|
| `app/main.py` | Aplicação FastAPI, `lifespan` (semeia o admin e inicia o `TrabalhadorMensageria`), handlers de erro, middlewares, `POST /auth/token`, `GET /health` e `GET /health/ready` |
| `app/adapters/inbound/http/` | Routers (`atendimento_routes`, `cadastro_routes`, `auth_routes`, `interno_routes`), schemas Pydantic de cada um e os middlewares de correlação e de cabeçalhos de segurança |
| `app/adapters/inbound/mensageria.py` | `tratar_mensagem`: valida o envelope, abre a transação do banco, chama o caso de uso e decide `ack` ou `nack` |
| `app/adapters/outbound/persistence/` | Repositórios SQLAlchemy: `os_repository`, `cliente_repository`, `auth_repository`, `saga_repository` e `outbox_repository` (outbox e inbox) |
| `app/adapters/outbound/mensageria.py` | `declarar_topologia` (exchanges, fila, DLQ) e `publicar_pendentes` (relay do outbox) |
| `app/application/use_cases/` | `criar_os`, `entregar_os`, `obter_os`, `listar_os`, `gerenciar_cadastro` e `saga_os` (`ProcessarEventoSagaUseCase`, `VerificarPrazosSagaUseCase`, `CancelarOSUseCase`) |
| `app/application/mensagens.py` | Montagem do envelope das mensagens |
| `app/application/eventos_os.py` | Log estruturado `os_status` a cada entrada da OS em um status |
| `app/domain/entities/` | `OrdemDeServico` (agregado com a máquina de estados), `ItemOS`, `HistoricoStatusOS`, `Cliente`, `Veiculo`, `Usuario` |
| `app/domain/saga.py` | `SagaAberturaOS`, `PassoSaga`, `EstadoSaga`, `PrazosSaga`, `Comando`, `ROTAS_DOS_COMANDOS`, `CONFIRMACOES` |
| `app/domain/ports/` | `OSRepositoryPort` (Protocol) |
| `app/domain/value_objects/` | `CpfCnpj` e `Placa`, com validação de dígitos verificadores e de formato |
| `app/domain/exceptions.py` | `NotFoundException` (404), `ConflictException` (409), `BusinessRuleException` (422) |
| `app/infrastructure/` | `config` (variáveis de ambiente), `database` (engine e fábrica de sessões), `orm_mapping` (tabelas e mapeamento), `security` (JWT, bcrypt, dependências de autenticação), `logging_config` (JSON e correlação), `rastreamento` (trace pela fila) e `trabalhador` (thread de mensageria) |

### Como um caso de uso se comporta

- Cada caso de uso é uma transação: a mudança de estado da OS e da Saga e as mensagens a publicar (outbox)
  são gravadas juntas, em um único `COMMIT`. Nada é publicado no RabbitMQ dentro da requisição HTTP.
- A Saga (`app/domain/saga.py`) é domínio puro: dado um evento, atualiza o próprio estado e a OS e **devolve a
  lista de comandos a enviar**. Quem grava o outbox é o caso de uso.
- Os casos de uso que alteram estado usam `SELECT ... FOR UPDATE`, sempre na ordem Saga e depois OS.
- `tests/test_dominio_puro.py` exercita o agregado sem banco e sem HTTP: é a rede de proteção da regra de
  dependência.

---

## 3. Responsabilidades e limites

| O OS Service faz | O OS Service não faz |
|---|---|
| Cadastra clientes (CPF ou CNPJ único e validado) e veículos (placa única e validada); ativa e desativa clientes | Não tem catálogo, preço de tabela, estoque, reserva nem fila de execução: são da Execução |
| Mantém usuários (`ADMIN`, `ATENDENTE`) e autentica funcionários por usuário e senha | Não gera orçamento, não registra aprovação do cliente, não cobra nem reembolsa: são do Billing |
| Valida o JWT de cliente emitido pela Lambda e confere se o cliente existe e está ativo a cada requisição | Não emite o JWT de cliente: é da Lambda |
| Abre a OS (cliente ativo, veículo do cliente), registra o histórico de status, consulta, entrega e cancela | Não acessa o banco de outro serviço e não chama outro serviço por HTTP: só envia e recebe mensagens |
| **Orquestra a Saga**: envia comandos, consome eventos, decide compensações e controla prazos | Não notifica o cliente: o orçamento é enviado pelo Billing |
| Responde à Lambda pela rota interna `/interno/clientes/autenticar` | Não envia CPF ou CNPJ em mensagem: o orçamento recebe só `id`, `nome`, `email` e `telefone` do cliente |

### De quem é cada dado

| Dado | Dono | Onde fica |
|---|---|---|
| Clientes, veículos, usuários | OS Service | `clientes`, `veiculos`, `usuarios` (PostgreSQL do OS Service) |
| OS, itens da OS e histórico de status | OS Service | `ordens_servico`, `itens_os`, `historico_status_os` |
| Estado da Saga, outbox e inbox | OS Service | `saga_instancia`, `outbox`, `mensagens_processadas` |
| Preço e descrição de cada item | Execução calcula; a OS guarda a cópia no item | `itens_os.preco_unitario`, `itens_os.descricao` |
| Catálogo, estoque, reservas, fila de execução | Execução | PostgreSQL da Execução |
| Orçamentos e pagamentos | Billing | MongoDB do Billing |
| Credencial de cliente (JWT) | Emitida pela Lambda, validada pelo OS Service | claims `sub` (id do cliente) e `tipo = cliente` |

---

## 4. Máquina de estados da OS

O agregado `OrdemDeServico` (`app/domain/entities/os.py`) é o único que muda o status da OS, e recusa qualquer
transição fora do mapa com `BusinessRuleException` (HTTP 422, mensagem `Transição inválida: X → Y. Permitidos: [...]`).

```mermaid
stateDiagram-v2
    [*] --> ABERTA
    ABERTA --> AGUARDANDO_APROVACAO: OrcamentoGerado
    ABERTA --> CANCELADA: reserva recusada, prazo ou cancelamento
    AGUARDANDO_APROVACAO --> AGUARDANDO_PAGAMENTO: OrcamentoAprovado
    AGUARDANDO_APROVACAO --> NEGADA: orçamento rejeitado pelo cliente
    AGUARDANDO_APROVACAO --> CANCELADA: orçamento expirado, prazo ou cancelamento
    AGUARDANDO_PAGAMENTO --> NA_FILA: OSEnfileirada
    AGUARDANDO_PAGAMENTO --> CANCELADA: pagamento recusado, falha ao enfileirar, prazo ou cancelamento
    NA_FILA --> EM_DIAGNOSTICO: DiagnosticoIniciado
    EM_DIAGNOSTICO --> EM_EXECUCAO: ExecucaoIniciada
    EM_EXECUCAO --> FINALIZADA: ExecucaoFinalizada
    FINALIZADA --> ENTREGUE: POST entregar
    ENTREGUE --> [*]
    NEGADA --> [*]
    CANCELADA --> [*]
```

| De | Para | Quem dispara |
|---|---|---|
| `ABERTA` | `AGUARDANDO_APROVACAO` | Saga, ao receber `OrcamentoGerado` |
| `ABERTA` | `CANCELADA` | Saga compensada nos passos de reserva e de geração do orçamento |
| `AGUARDANDO_APROVACAO` | `AGUARDANDO_PAGAMENTO` | Saga, ao receber `OrcamentoAprovado` |
| `AGUARDANDO_APROVACAO` | `NEGADA` | Saga compensada com motivo `ORCAMENTO_REJEITADO` |
| `AGUARDANDO_APROVACAO` | `CANCELADA` | Saga compensada por orçamento expirado, prazo ou cancelamento pela oficina |
| `AGUARDANDO_PAGAMENTO` | `NA_FILA` | Saga, ao receber `OSEnfileirada` |
| `AGUARDANDO_PAGAMENTO` | `CANCELADA` | Saga compensada por pagamento recusado ou expirado, enfileiramento recusado, prazo ou cancelamento |
| `NA_FILA` | `EM_DIAGNOSTICO` | evento `DiagnosticoIniciado` da Execução |
| `EM_DIAGNOSTICO` | `EM_EXECUCAO` | evento `ExecucaoIniciada` (grava `iniciado_em`) |
| `EM_EXECUCAO` | `FINALIZADA` | evento `ExecucaoFinalizada` (grava `finalizado_em`) |
| `FINALIZADA` | `ENTREGUE` | `POST /atendimento/os/{id}/entregar`, a única transição manual |

Regras do agregado:

- `ENTREGUE`, `NEGADA` e `CANCELADA` são terminais.
- Durante as compensações o status da OS não muda; ele só vira `NEGADA` ou `CANCELADA` quando a última
  compensação é confirmada. Em particular, a OS permanece em `AGUARDANDO_PAGAMENTO` enquanto a Saga está no
  passo `ENFILEIRANDO`.
- Os eventos da Execução percorrem os status intermediários até o alvo (`avancar_execucao`): se
  `ExecucaoFinalizada` chega com a OS em `NA_FILA`, a OS passa por `EM_DIAGNOSTICO` e `EM_EXECUCAO` e
  grava uma linha de histórico para cada status. Repetir um evento já aplicado não faz nada.
- Cada entrada em um status vira uma linha de `historico_status_os`, devolvida no campo `historico` de
  `GET /atendimento/os/{id}`. A diferença entre linhas consecutivas é o tempo no status, usado por
  `GET /atendimento/os/metricas/tempo-medio` e pelo dashboard.
- A OS nasce sem preço. Os itens são criados só com `tipo`, `item_id` e `quantidade`; `descricao`,
  `preco_unitario` e `valor_total` chegam na mensagem `PecasReservadas` (`OrdemDeServico.precificar`).

---

## 5. A Saga

Abrir uma OS passa por três serviços: reservar peças (Execução), orçar e cobrar (Billing) e enfileirar para
execução (Execução). Cada passo grava no banco do seu serviço, então não existe transação única. Se um passo
falha, os anteriores precisam ser desfeitos. A solução é uma **Saga orquestrada**, com o orquestrador dentro do
OS Service (ADR-018).

### 5.1 Por que orquestrada

| Critério | Orquestrada (escolhida) | Coreografada |
|---|---|---|
| Onde está a regra do fluxo | Numa classe, num serviço (`SagaAberturaOS`) | Espalhada pelos três serviços |
| Compensação | Explícita, na ordem inversa, disparada pelo orquestrador | Cada serviço escuta as falhas dos outros e decide sozinho |
| Estado da Saga | Uma linha consultável em `saga_instancia` | Reconstruído a partir dos eventos |
| Prazo de um passo | Varredura de `saga_instancia.expira_em` | Cada participante precisa de um temporizador próprio |
| Teste do fluxo completo | BDD num repositório, com participantes simulados | Exige subir os três serviços |
| Acoplamento | O orquestrador conhece todos os passos | Nenhum serviço conhece o fluxo inteiro |

Motivos:

- O fluxo é linear e tem um dono natural: a OS. Quem precisa saber em que passo ela está é o OS Service.
- A compensação mais delicada, reembolsar quando a execução não pode começar, depende de saber que o pagamento
  foi confirmado. Na orquestração essa informação está numa linha (`pagamento_confirmado`); na coreografia, o
  Billing teria de escutar eventos da Execução e decidir por conta própria.
- Com três serviços, o custo da orquestração (um ponto que conhece o fluxo) é pequeno, e o ganho em clareza,
  teste e demonstração é grande.

Consequências: o OS Service concentra o conhecimento do fluxo, e um passo novo muda o orquestrador e o
participante. Execução e Billing não se conhecem: só respondem a comandos. Toda compensação precisa ser
idempotente, porque o orquestrador pode repeti-la. A Saga pode parar em `FALHA_NA_COMPENSACAO` (reembolso
recusado pelo provedor), e esse estado gera alerta e exige ação humana.

### 5.2 Peças da implementação

| Peça | Papel |
|---|---|
| `SagaAberturaOS` (`app/domain/saga.py`) | Estado de uma Saga e regras de transição. Cada método devolve os comandos a enviar |
| `saga_instancia` | Uma linha por OS: passo, estado, motivo, compensações pendentes, flags de efeito, prazo |
| `CriarOSUseCase` | Abre a OS em `ABERTA` e a Saga em `RESERVANDO_PECAS`, e grava `ReservarPecas` no outbox, na mesma transação |
| `ProcessarEventoSagaUseCase` | Aplica um evento da Execução ou do Billing à Saga, uma única vez por `message_id` |
| `VerificarPrazosSagaUseCase` | Varre as Sagas com prazo vencido e aplica a regra de cada passo |
| `CancelarOSUseCase` | Cancelamento pela oficina: dispara as compensações |
| `TrabalhadorMensageria` | Thread que consome eventos, publica o outbox e dispara a varredura de prazos |

### 5.3 Passos e estados

**Passos** (`PassoSaga`, coluna `saga_instancia.passo`):

| Passo | Comando enviado ao entrar | Espera | Sucesso | Falha | Status da OS durante o passo |
|---|---|---|---|---|---|
| `RESERVANDO_PECAS` | `ReservarPecas` | `PecasReservadas` ou `ReservaRecusada` | marca `reserva_feita`, precifica a OS, vai para `GERANDO_ORCAMENTO` | `ReservaRecusada`: compensa (nada a desfazer) | `ABERTA` |
| `GERANDO_ORCAMENTO` | `GerarOrcamento` | `OrcamentoGerado` | guarda `orcamento_id`, marca `orcamento_ativo`, OS vai para `AGUARDANDO_APROVACAO`, Saga vai para `AGUARDANDO_APROVACAO` | só por prazo | `ABERTA` |
| `AGUARDANDO_APROVACAO` | nenhum | `OrcamentoAprovado` ou `OrcamentoRejeitado` | OS vai para `AGUARDANDO_PAGAMENTO`, Saga vai para `AGUARDANDO_PAGAMENTO` | `OrcamentoRejeitado`: compensa com o motivo do evento | `AGUARDANDO_APROVACAO` |
| `AGUARDANDO_PAGAMENTO` | `Cobrar` | `CobrancaCriada` (informativo), depois `PagamentoConfirmado` ou `PagamentoRecusado` | guarda `pagamento_id`, marca `pagamento_confirmado`, vai para `ENFILEIRANDO` | `PagamentoRecusado`: compensa com o motivo do evento | `AGUARDANDO_PAGAMENTO` |
| `ENFILEIRANDO` | `EnfileirarOS` | `OSEnfileirada` ou `EnfileiramentoRecusado` | zera `reserva_feita` (a Execução consumiu a reserva), OS vai para `NA_FILA`, Saga vai para `EM_EXECUCAO` | `EnfileiramentoRecusado`: compensa com motivo `ENFILEIRAMENTO_RECUSADO` | `AGUARDANDO_PAGAMENTO` |
| `EM_EXECUCAO` | nenhum | `DiagnosticoIniciado`, `ExecucaoIniciada`, `ExecucaoFinalizada` | a OS avança até `FINALIZADA` e a Saga vai para `CONCLUIDA` | não há | `NA_FILA`, `EM_DIAGNOSTICO`, `EM_EXECUCAO`, `FINALIZADA` |

**Estados** (`EstadoSaga`, coluna `saga_instancia.estado`):

| Estado | Significado |
|---|---|
| `EM_ANDAMENTO` | A Saga segue o caminho feliz |
| `COMPENSANDO` | Uma falha, um prazo ou um cancelamento disparou as compensações; a lista `compensacoes` guarda as pendentes |
| `CONCLUIDA` | A execução terminou (`ExecucaoFinalizada`) |
| `COMPENSADA` | Todas as compensações foram confirmadas e a OS terminou em `NEGADA` ou `CANCELADA` |
| `FALHA_NA_COMPENSACAO` | O Billing informou `ReembolsoFalhou`; a Saga para, sem prazo, e exige ação humana |

```mermaid
stateDiagram-v2
    [*] --> EM_ANDAMENTO: OS aberta
    EM_ANDAMENTO --> CONCLUIDA: ExecucaoFinalizada
    EM_ANDAMENTO --> COMPENSANDO: falha, prazo ou cancelamento
    EM_ANDAMENTO --> COMPENSADA: falha sem nada a desfazer
    COMPENSANDO --> COMPENSANDO: confirmação, ainda restam compensações
    COMPENSANDO --> COMPENSADA: última confirmação
    COMPENSANDO --> FALHA_NA_COMPENSACAO: ReembolsoFalhou
    COMPENSADA --> COMPENSANDO: PagamentoConfirmado tardio
```

### 5.4 Caminho feliz

Toda seta entre serviços passa pelo RabbitMQ: o OS Service grava o comando no outbox e o relay o publica;
os eventos chegam à fila `os-service.eventos`. Setas cheias são comandos ou chamadas HTTP; setas tracejadas são
eventos.

```mermaid
sequenceDiagram
    autonumber
    actor F as Funcionário
    actor C as Cliente
    participant OS as OS Service<br/>orquestrador
    participant EX as Execução
    participant BI as Billing
    participant MP as Mercado Pago

    F->>OS: POST /atendimento/os
    Note over OS: grava a OS em ABERTA, a Saga em RESERVANDO_PECAS<br/>e o comando no outbox, numa só transação
    OS-->>F: 201 ABERTA
    OS->>EX: ReservarPecas
    EX-->>OS: PecasReservadas (itens com preço e valor total)
    Note over OS: precifica a OS, Saga em GERANDO_ORCAMENTO
    OS->>BI: GerarOrcamento
    BI-->>OS: OrcamentoGerado
    Note over OS: OS em AGUARDANDO_APROVACAO
    C->>BI: POST /billing/orcamentos/{os_id}/aprovar
    BI-->>OS: OrcamentoAprovado
    Note over OS: OS em AGUARDANDO_PAGAMENTO
    OS->>BI: Cobrar
    BI->>MP: cria o pedido com QR dinâmico
    BI-->>OS: CobrancaCriada
    C->>MP: paga o QR
    MP-->>BI: pagamento confirmado (consulta periódica ou webhook)
    BI-->>OS: PagamentoConfirmado
    Note over OS: Saga em ENFILEIRANDO
    OS->>EX: EnfileirarOS
    EX-->>OS: OSEnfileirada
    Note over OS: OS em NA_FILA, Saga em EM_EXECUCAO
    EX-->>OS: DiagnosticoIniciado
    EX-->>OS: ExecucaoIniciada
    EX-->>OS: ExecucaoFinalizada
    Note over OS: OS em FINALIZADA, Saga CONCLUIDA
    F->>OS: POST /atendimento/os/{id}/entregar
    OS-->>F: 200 ENTREGUE
```

Com o provedor `simulado` do Billing, o pagamento é aprovado por uma rota de teste do próprio Billing no lugar do
QR no app do Mercado Pago (ver o README do `tech-challenge-f4-billing-service`).

### 5.5 Roteamento dos comandos e confirmações

Definidos em `app/domain/saga.py` e iguais ao contrato AsyncAPI.

| Comando (`ROTAS_DOS_COMANDOS`) | Routing key | Destino |
|---|---|---|
| `ReservarPecas` | `execucao.reservar-pecas` | Execução |
| `LiberarReserva` | `execucao.liberar-reserva` | Execução |
| `EnfileirarOS` | `execucao.enfileirar-os` | Execução |
| `GerarOrcamento` | `billing.gerar-orcamento` | Billing |
| `Cobrar` | `billing.cobrar` | Billing |
| `CancelarOrcamento` | `billing.cancelar-orcamento` | Billing |
| `Reembolsar` | `billing.reembolsar` | Billing |

| Evento de confirmação (`CONFIRMACOES`) | Compensação confirmada | Coluna que volta a `false` |
|---|---|---|
| `ReservaLiberada` | `LiberarReserva` | `reserva_feita` |
| `OrcamentoCancelado` | `CancelarOrcamento` | `orcamento_ativo` |
| `PagamentoReembolsado` | `Reembolsar` | `pagamento_confirmado` |

| Evento da Execução (`AVANCOS_DA_EXECUCAO`) | Status a que a OS chega |
|---|---|
| `DiagnosticoIniciado` | `EM_DIAGNOSTICO` |
| `ExecucaoIniciada` | `EM_EXECUCAO` |
| `ExecucaoFinalizada` | `FINALIZADA` |

### 5.6 Como a Saga reage a um evento

`SagaAberturaOS.ao_receber` avalia as regras nesta ordem. A primeira que casa decide:

1. `CobrancaCriada`: sempre registra o `pagamento_id`. Se a Saga está em `EM_ANDAMENTO` no passo
   `AGUARDANDO_PAGAMENTO`, o prazo passa a ser o vencimento da cobrança mais 5 minutos de folga.
2. `PagamentoConfirmado` com a Saga em `COMPENSANDO` ou `COMPENSADA`: é um **pagamento tardio** (seção 5.9).
3. Saga em `COMPENSANDO`: só aceita `ReembolsoFalhou` e o evento de confirmação da compensação que está em
   curso (a primeira da lista). Qualquer outro evento é descartado.
4. Eventos da Execução (`DiagnosticoIniciado`, `ExecucaoIniciada`, `ExecucaoFinalizada`) com a Saga no passo
   `EM_EXECUCAO`: avançam a OS. `ExecucaoFinalizada` leva a Saga a `CONCLUIDA`.
5. Saga fora de `EM_ANDAMENTO` (`CONCLUIDA`, `COMPENSADA`, `FALHA_NA_COMPENSACAO`): descarta.
6. Par (passo atual, tipo do evento) da tabela da seção 5.3: executa a transição. Qualquer outro par é
   descartado.

**Descartar** significa: registrar um log `WARNING` com `evento = saga_evento_descartado`, não alterar a Saga e
confirmar a mensagem com `ack`. Isso absorve eventos repetidos, atrasados ou fora de ordem, que o RabbitMQ não
impede entre filas diferentes.

### 5.7 Falhas e compensações

Quando algo falha, a Saga desfaz o que já foi feito, **do passo mais recente para o mais antigo**, **uma
compensação por vez**: só envia a próxima depois de receber a confirmação da anterior.

A lista de compensações é montada a partir das flags de efeito, nesta ordem fixa:

1. `Reembolsar`, se `pagamento_confirmado`.
2. `CancelarOrcamento`, se `orcamento_ativo`.
3. `LiberarReserva`, se `reserva_feita`.

Cada comando de compensação leva `{"motivo": "<motivo da Saga>"}`. Ao confirmar a última, a Saga vai para
`COMPENSADA` e a OS vai para **`NEGADA` se o motivo for `ORCAMENTO_REJEITADO`, e para `CANCELADA` em qualquer
outro caso**.

| Ponto do fluxo | Gatilho | Compensações, na ordem de envio | Motivo gravado | OS ao final |
|---|---|---|---|---|
| Reserva de peças | `ReservaRecusada` | nenhuma (nada foi reservado) | `RESERVA_RECUSADA` | `CANCELADA` |
| Aprovação do orçamento | `OrcamentoRejeitado` com `ORCAMENTO_REJEITADO` (o cliente recusou) | `LiberarReserva` | `ORCAMENTO_REJEITADO` | `NEGADA` |
| Aprovação do orçamento | `OrcamentoRejeitado` com `ORCAMENTO_EXPIRADO` (o Billing expirou o orçamento) | `LiberarReserva` | `ORCAMENTO_EXPIRADO` | `CANCELADA` |
| Pagamento | `PagamentoRecusado` com `PAGAMENTO_RECUSADO` | `CancelarOrcamento`, `LiberarReserva` | `PAGAMENTO_RECUSADO` | `CANCELADA` |
| Pagamento | `PagamentoRecusado` com `PAGAMENTO_EXPIRADO` (QR vencido) | `CancelarOrcamento`, `LiberarReserva` | `PAGAMENTO_EXPIRADO` | `CANCELADA` |
| Enfileiramento | `EnfileiramentoRecusado` | `Reembolsar`, `CancelarOrcamento`, `LiberarReserva` | `ENFILEIRAMENTO_RECUSADO` | `CANCELADA` |
| Cancelamento pela oficina, antes do pagamento | `POST /atendimento/os/{id}/cancelar` | conforme o passo (seção 5.10) | `CANCELADA_PELA_OFICINA` | `CANCELADA` |
| Prazo de um passo esgotado, antes do pagamento | varredura de prazos | conforme o passo (seção 5.8) | `PRAZO_ESGOTADO` | `CANCELADA` |
| Prazo esgotado no enfileiramento | varredura de prazos, depois de 3 reenvios | `Reembolsar`, `CancelarOrcamento`, `LiberarReserva` | `PRAZO_ESGOTADO` | `CANCELADA` |
| Pagamento confirmado depois da decisão de compensar | `PagamentoConfirmado` tardio | acrescenta `Reembolsar` (seção 5.9) | mantém o motivo | `CANCELADA` |
| Reembolso recusado pelo provedor | `ReembolsoFalhou` | nenhuma; a Saga para | mantém o motivo | inalterada |

Quando o Billing já encerrou o orçamento (`OrcamentoRejeitado`), a Saga zera `orcamento_ativo` antes de
compensar, e por isso não envia `CancelarOrcamento` nos dois casos de orçamento rejeitado ou expirado.

Pior caso, enfileiramento recusado depois do pagamento:

```mermaid
sequenceDiagram
    autonumber
    participant OS as OS Service
    participant EX as Execução
    participant BI as Billing

    EX-->>OS: EnfileiramentoRecusado
    Note over OS: estado COMPENSANDO<br/>compensacoes = Reembolsar, CancelarOrcamento, LiberarReserva
    OS->>BI: Reembolsar
    BI-->>OS: PagamentoReembolsado
    OS->>BI: CancelarOrcamento
    BI-->>OS: OrcamentoCancelado
    OS->>EX: LiberarReserva
    EX-->>OS: ReservaLiberada
    Note over OS: estado COMPENSADA, OS em CANCELADA
```

**`FALHA_NA_COMPENSACAO`.** Se o Billing responder `ReembolsoFalhou` durante a compensação, a Saga para: o
estado muda, `expira_em` fica nulo e os eventos seguintes são descartados. A OS continua no status em que
estava (por exemplo `AGUARDANDO_PAGAMENTO`) e a lista `compensacoes` mantém o que faltou. O alerta
**Saga parada em falha na compensação**, do New Relic, abre incidente. A API não tem rota para retomar a Saga:
a correção é manual, no Billing e no banco.

### 5.8 Timeouts

Cada passo que depende de uma resposta tem prazo, gravado em `saga_instancia.expira_em`. A
`TrabalhadorMensageria` varre as Sagas vencidas a cada `SAGA_VARREDURA_SEGUNDOS` (30 s), em lotes de 50, com
`FOR UPDATE SKIP LOCKED`; por isso um prazo pode ser aplicado até 30 s depois de vencer.

| Passo | Prazo | Quando esgota |
|---|---|---|
| `RESERVANDO_PECAS` | 5 min (`SAGA_PRAZO_RESPOSTA_SEGUNDOS`) | compensa: `LiberarReserva` |
| `GERANDO_ORCAMENTO` | 5 min | compensa: `CancelarOrcamento`, `LiberarReserva` |
| `AGUARDANDO_APROVACAO` | o menor entre o vencimento do orçamento mais 5 min de folga e 24 h (`SAGA_PRAZO_APROVACAO_HORAS`) | compensa: `CancelarOrcamento`, `LiberarReserva` |
| `AGUARDANDO_PAGAMENTO` | 5 min até a `CobrancaCriada`; depois, o vencimento da cobrança mais 5 min de folga | compensa: `CancelarOrcamento`, `LiberarReserva` |
| `ENFILEIRANDO` | 5 min | reenvia `EnfileirarOS` até 3 vezes, cada uma com novo prazo de 5 min; na quarta expiração compensa: `Reembolsar`, `CancelarOrcamento`, `LiberarReserva` |
| `EM_EXECUCAO` | sem prazo | nada: a execução da oficina pode demorar |
| Estado `COMPENSANDO` | 5 min por compensação | reenvia o mesmo comando de compensação, sem limite de repetições |
| Estado `FALHA_NA_COMPENSACAO` | sem prazo | nada |

Pontos que explicam as regras:

- **Antes do pagamento, o prazo esgotado compensa.** Sem resposta, o comando do passo em curso pode ter sido
  executado pelo participante. A Saga assume que sim e marca a flag do passo (`reserva_feita` em
  `RESERVANDO_PECAS`, `orcamento_ativo` em `GERANDO_ORCAMENTO`) antes de compensar. Como as compensações são
  idempotentes, desfazer algo que não existe responde com o evento de confirmação, sem erro.
- **Depois do pagamento, o prazo tenta seguir em frente.** O dinheiro já foi recebido; por isso o enfileiramento
  é repetido antes de desistir e reembolsar.
- O prazo de 5 minutos e a folga de 5 minutos são `PrazosSaga` (`resposta` e `folga`). O prazo de resposta e o
  de aprovação vêm das variáveis de ambiente; a folga é fixa.

### 5.9 Pagamento tardio

O cliente pode pagar o QR no instante em que a Saga já decidiu compensar (cancelamento pela oficina, prazo
esgotado ou recusa). O evento `PagamentoConfirmado` então chega com a Saga em `COMPENSANDO` ou `COMPENSADA`, e o
dinheiro não pode ficar retido:

- Saga em **`COMPENSANDO`**: marca `pagamento_confirmado` e **acrescenta `Reembolsar` ao fim** da lista de
  compensações. A compensação em curso não é interrompida; o reembolso sai depois das pendentes.
- Saga em **`COMPENSADA`**: marca `pagamento_confirmado`, acrescenta `Reembolsar`, volta o estado para
  `COMPENSANDO` e envia `Reembolsar` na hora. A OS continua `CANCELADA`.
- Um segundo `PagamentoConfirmado` para a mesma Saga é descartado.

### 5.10 Cancelamento pela oficina

`POST /atendimento/os/{id}/cancelar` (funcionário) só é aceito com a Saga em `EM_ANDAMENTO` e em um dos passos
`RESERVANDO_PECAS`, `GERANDO_ORCAMENTO`, `AGUARDANDO_APROVACAO` ou `AGUARDANDO_PAGAMENTO`. Fora disso a API
responde 422 (`A OS não pode mais ser cancelada (passo X, estado Y)`). Em particular, a partir do
`PagamentoConfirmado` (passo `ENFILEIRANDO`) a oficina não cancela mais.

| Passo no momento do cancelamento | Compensações enviadas |
|---|---|
| `RESERVANDO_PECAS` | `LiberarReserva` |
| `GERANDO_ORCAMENTO` | `CancelarOrcamento`, `LiberarReserva` |
| `AGUARDANDO_APROVACAO` | `CancelarOrcamento`, `LiberarReserva` |
| `AGUARDANDO_PAGAMENTO` | `CancelarOrcamento`, `LiberarReserva`; e `Reembolsar`, se um pagamento chegar no meio (seção 5.9) |

A resposta `200` devolve a OS ainda no status anterior, com a Saga em `COMPENSANDO`; a OS só vira `CANCELADA`
quando a última confirmação chega.

### 5.11 Persistência da Saga

Uma linha de `saga_instancia` por OS (`os_id` único). O `id` da linha é o `saga_id` de todas as mensagens
dessa OS. As colunas estão descritas em [`docs/modelo-de-dados.md`](docs/modelo-de-dados.md).

O estado atual aparece no campo `saga` de `GET /atendimento/os/{id}` (OS recém-aberta):

```json
{
  "id": "1b03139c-6b0b-4ea2-b3cb-5a5992d8b135",
  "status": "ABERTA",
  "valor_total": null,
  "saga": {
    "id": "6c8f0e4a-5a52-4a36-93a5-0f7a1d7f2a10",
    "passo": "RESERVANDO_PECAS",
    "estado": "EM_ANDAMENTO",
    "motivo": null,
    "compensacoes": [],
    "expira_em": "2026-01-01T12:05:00Z"
  }
}
```

Para acompanhar uma Saga:

| Onde | Como |
|---|---|
| API | `GET /atendimento/os/{id}`: campos `status`, `historico` e `saga` |
| Banco | `SELECT passo, estado, motivo, compensacoes, expira_em FROM saga_instancia WHERE os_id = '<id>'` |
| Logs e New Relic | `SELECT * FROM Log WHERE evento = 'saga' AND saga_id = '<id>'`: um log por passo, com `gatilho`, `passo`, `estado`, `motivo` e `comandos` |
| RabbitMQ | painel do broker: filas `os-service.eventos`, `execucao-service.comandos`, `billing-service.comandos` e as DLQs |

Cada passo da Saga produz um log estruturado:

```json
{"level": "INFO", "logger": "oficina.saga",
 "message": "Saga 6c8f0e4a-... · PecasReservadas → GERANDO_ORCAMENTO/EM_ANDAMENTO",
 "correlation_id": "6c8f0e4a-...", "evento": "saga", "saga_id": "6c8f0e4a-...", "os_id": "1b03139c-...",
 "gatilho": "PecasReservadas", "passo": "GERANDO_ORCAMENTO", "estado": "EM_ANDAMENTO", "comandos": ["GerarOrcamento"]}
```

### 5.12 Garantias de entrega: outbox, inbox e `TrabalhadorMensageria`

O detalhe da mensageria está na seção 6. O que sustenta a Saga:

- **Outbox transacional.** O comando é gravado em `outbox` na mesma transação que muda a Saga e a OS. Se o
  processo cair depois do `COMMIT`, a mensagem continua no banco e o relay a publica depois. Se o `COMMIT`
  falhar, nenhuma mensagem sai.
- **Inbox idempotente.** O consumidor grava o `message_id` em `mensagens_processadas` na mesma transação do
  efeito. Mensagem repetida não repete o efeito.
- **`ack` manual**, só depois do `COMMIT`.
- **`TrabalhadorMensageria`.** Thread daemon que, em cada processo da aplicação, faz as três tarefas: consome
  `os-service.eventos`, publica o outbox e varre os prazos.

---

## 6. Mensageria

O contrato completo (AsyncAPI 3.0) está em `tech-challenge-f4-plataforma/docs/contratos/asyncapi.yaml`.
Este serviço **publica comandos** e **consome eventos**; ele não publica eventos.

### 6.1 Topologia

Declarada por `declarar_topologia` a cada conexão (a declaração é idempotente, então a ordem de subida dos
serviços não importa).

| Objeto | Tipo | Detalhe |
|---|---|---|
| `oficina.comandos` | exchange topic, durável | onde o OS Service publica os comandos |
| `oficina.eventos` | exchange topic, durável | onde a Execução e o Billing publicam os eventos |
| `oficina.dlx` | exchange topic, durável | destino das mensagens mortas |
| `os-service.eventos` | fila quorum | ligada a `oficina.eventos` por `execucao.#` e `billing.#`; `x-delivery-limit` 5; `x-dead-letter-exchange` `oficina.dlx`; `x-dead-letter-routing-key` `os-service.eventos` |
| `os-service.eventos.dlq` | fila quorum | ligada a `oficina.dlx` pela chave `os-service.eventos`; ninguém a consome |

As filas de comandos `execucao-service.comandos` e `billing-service.comandos` (ligadas a `oficina.comandos` por
`execucao.#` e `billing.#`) são declaradas pelos próprios participantes.

### 6.2 Envelope

Todas as mensagens têm o mesmo corpo JSON, montado em `app/application/mensagens.py`:

```json
{
  "message_id": "0d6f3c1e-8a4b-4c52-9d3e-1f2a3b4c5d6e",
  "tipo": "ReservarPecas",
  "versao": 1,
  "saga_id": "6c8f0e4a-5a52-4a36-93a5-0f7a1d7f2a10",
  "os_id": "1b03139c-6b0b-4ea2-b3cb-5a5992d8b135",
  "ocorrido_em": "2026-01-01T12:00:00Z",
  "origem": "os-service",
  "payload": { "itens": [ { "tipo": "PECA", "item_id": "cbd4af95-...", "quantidade": 2 } ] }
}
```

| Campo | Descrição |
|---|---|
| `message_id` | UUID único por mensagem; chave de idempotência do consumidor |
| `tipo` | nome da mensagem, por exemplo `ReservarPecas` |
| `versao` | sempre `1` |
| `saga_id` | id da Saga (`saga_instancia.id`) |
| `os_id` | id da OS; uma Saga por OS |
| `ocorrido_em` | RFC 3339, UTC |
| `origem` | `os-service`, `execucao-service` ou `billing-service` |
| `payload` | conteúdo específico de cada mensagem |

Valores monetários trafegam como texto com duas casas (`"150.00"`). CPF e CNPJ não trafegam. O contexto do trace
distribuído **não vai no corpo**: viaja nos cabeçalhos AMQP, guardados na coluna `outbox.cabecalhos` até o
relay publicar.

Propriedades AMQP que o relay define em cada mensagem:

| Propriedade | Valor |
|---|---|
| `message_id` | igual ao `message_id` do corpo |
| `type` | igual ao `tipo` |
| `correlation_id` | `saga_id` |
| `content_type` | `application/json` |
| `delivery_mode` | `2` (persistente) |
| `headers` | contexto do trace distribuído do agente New Relic (`traceparent`, `tracestate`, `newrelic`); vazio quando o agente está desligado |

### 6.3 Comandos publicados

Todos saem pela exchange `oficina.comandos`.

| Comando | Routing key | Payload | Quando é enviado |
|---|---|---|---|
| `ReservarPecas` | `execucao.reservar-pecas` | `itens`: lista de `{tipo, item_id, quantidade}` | ao abrir a OS |
| `GerarOrcamento` | `billing.gerar-orcamento` | `cliente` (`id`, `nome`, `email`, `telefone`), `itens` precificados, `valor_total` | ao receber `PecasReservadas` |
| `Cobrar` | `billing.cobrar` | `orcamento_id` | ao receber `OrcamentoAprovado` |
| `EnfileirarOS` | `execucao.enfileirar-os` | `veiculo_id` | ao receber `PagamentoConfirmado`; reenviado por prazo, até 3 vezes |
| `LiberarReserva` | `execucao.liberar-reserva` | `motivo` | compensação de `reserva_feita` |
| `CancelarOrcamento` | `billing.cancelar-orcamento` | `motivo` | compensação de `orcamento_ativo` |
| `Reembolsar` | `billing.reembolsar` | `motivo` | compensação de `pagamento_confirmado` |

`motivo` é um de: `ORCAMENTO_REJEITADO`, `ORCAMENTO_EXPIRADO`, `PAGAMENTO_RECUSADO`, `PAGAMENTO_EXPIRADO`,
`ENFILEIRAMENTO_RECUSADO`, `PRAZO_ESGOTADO`, `CANCELADA_PELA_OFICINA` (e `RESERVA_RECUSADA`, que não gera
comando porque nada foi reservado).

### 6.4 Eventos consumidos

Todos chegam pela exchange `oficina.eventos`, na fila `os-service.eventos`.

| Evento | Routing key | Origem | O que o OS Service usa do payload | Efeito |
|---|---|---|---|---|
| `PecasReservadas` | `execucao.pecas-reservadas` | Execução | `itens` (com `descricao`, `preco_unitario`) e `valor_total` | precifica a OS; envia `GerarOrcamento` |
| `ReservaRecusada` | `execucao.reserva-recusada` | Execução | nada (o motivo da Saga é `RESERVA_RECUSADA`) | compensa; OS `CANCELADA` |
| `ReservaLiberada` | `execucao.reserva-liberada` | Execução | nada | confirma `LiberarReserva` |
| `OSEnfileirada` | `execucao.os-enfileirada` | Execução | nada | OS em `NA_FILA` |
| `EnfileiramentoRecusado` | `execucao.enfileiramento-recusado` | Execução | nada (o motivo da Saga é `ENFILEIRAMENTO_RECUSADO`) | compensa |
| `DiagnosticoIniciado` | `execucao.diagnostico-iniciado` | Execução | nada | OS em `EM_DIAGNOSTICO` |
| `ExecucaoIniciada` | `execucao.execucao-iniciada` | Execução | nada | OS em `EM_EXECUCAO` |
| `ExecucaoFinalizada` | `execucao.execucao-finalizada` | Execução | nada | OS em `FINALIZADA`; Saga `CONCLUIDA` |
| `OrcamentoGerado` | `billing.orcamento-gerado` | Billing | `orcamento_id`, `expira_em` | OS em `AGUARDANDO_APROVACAO`; define o prazo de aprovação |
| `OrcamentoAprovado` | `billing.orcamento-aprovado` | Billing | nada | OS em `AGUARDANDO_PAGAMENTO`; envia `Cobrar` |
| `OrcamentoRejeitado` | `billing.orcamento-rejeitado` | Billing | `motivo` (`ORCAMENTO_REJEITADO` ou `ORCAMENTO_EXPIRADO`) | compensa |
| `OrcamentoCancelado` | `billing.orcamento-cancelado` | Billing | nada | confirma `CancelarOrcamento` |
| `CobrancaCriada` | `billing.cobranca-criada` | Billing | `pagamento_id`, `expira_em` | guarda o pagamento; ajusta o prazo |
| `PagamentoConfirmado` | `billing.pagamento-confirmado` | Billing | `pagamento_id` | envia `EnfileirarOS`; ou reembolso, se tardio |
| `PagamentoRecusado` | `billing.pagamento-recusado` | Billing | `motivo` (`PAGAMENTO_RECUSADO` ou `PAGAMENTO_EXPIRADO`) | compensa |
| `PagamentoReembolsado` | `billing.pagamento-reembolsado` | Billing | nada | confirma `Reembolsar` |
| `ReembolsoFalhou` | `billing.reembolso-falhou` | Billing | nada | Saga em `FALHA_NA_COMPENSACAO` |

### 6.5 Publicação: outbox e relay

```mermaid
sequenceDiagram
    autonumber
    participant UC as Caso de uso
    participant DB as PostgreSQL
    participant RL as Relay<br/>(TrabalhadorMensageria)
    participant MQ as RabbitMQ

    UC->>DB: atualiza a OS e a Saga, insere no outbox, COMMIT
    RL->>DB: SELECT pendentes FOR UPDATE SKIP LOCKED
    RL->>MQ: basic_publish com mandatory
    MQ-->>RL: confirmação do broker (publisher confirm)
    RL->>DB: publicado_em = agora, COMMIT
```

- O `OutboxRepositoryAdapter.adicionar` grava `exchange`, `rota`, `tipo`, o envelope em `corpo` e, em
  `cabecalhos`, o contexto do trace atual.
- A cada volta do laço (cerca de 1 s) o relay lê até 50 mensagens com `publicado_em IS NULL`, em ordem de
  `criado_em`, e publica com **publisher confirms** (`confirm_delivery`) e `mandatory=True`. Só marca
  `publicado_em` depois da confirmação.
- Se o broker recusar uma mensagem (`UnroutableError` ou `NackError`, por exemplo quando a fila do participante
  ainda não existe), o relay registra `publicacao_recusada`, **para no primeiro recusado** e tenta de novo na
  volta seguinte. A mensagem continua no outbox.
- O relay pode publicar a mesma mensagem duas vezes (o processo cai depois de publicar e antes de marcar). A
  inbox do consumidor absorve a repetição.
- Vários processos rodam o relay ao mesmo tempo: `SKIP LOCKED` faz cada um pegar linhas diferentes.

### 6.6 Consumo: inbox, `ack` manual, DLQ e retentativas

```mermaid
sequenceDiagram
    autonumber
    participant MQ as RabbitMQ
    participant CO as Consumidor<br/>(tratar_mensagem)
    participant DB as PostgreSQL

    MQ->>CO: entrega o evento de os-service.eventos
    CO->>CO: valida o envelope e retoma o trace
    CO->>DB: INSERT em mensagens_processadas (savepoint)
    alt message_id já existe
        CO->>MQ: ack, sem efeito
    else primeira entrega
        CO->>DB: trava a Saga e a OS (FOR UPDATE)
        CO->>DB: aplica o evento, grava a OS, a Saga e o outbox, COMMIT
        CO->>MQ: ack
    end
```

`tratar_mensagem` decide o destino de cada entrega:

| Situação | O que acontece | Log (`evento`) |
|---|---|---|
| Evento aplicado | `COMMIT` e `ack` | `saga` e `os_status` |
| `message_id` já processado | `ack`, sem efeito | `mensagem_repetida` |
| Evento que não cabe no passo da Saga | `ack`, sem efeito na Saga | `saga_evento_descartado` |
| JSON inválido, `tipo`, `saga_id` ou `message_id` ausentes, ou UUID inválido | `nack` sem reentrega: vai direto para a DLQ | `mensagem_invalida` |
| Saga ou OS inexistente | `rollback` e `nack` sem reentrega: vai para a DLQ | `mensagem_sem_saga` |
| Erro inesperado (por exemplo o banco fora do ar) | `rollback` e `nack` com reentrega | `falha_no_consumo` |
| Reentregas esgotadas | o RabbitMQ envia à DLQ depois de seis entregas sem `ack` (a primeira e cinco reentregas, `x-delivery-limit` 5) | |

- **`ack` manual**: o `basic_ack` só acontece depois do `COMMIT`. O consumo usa `basic_qos(prefetch_count=10)`.
- **Inbox**: o `INSERT` em `mensagens_processadas` roda dentro de um savepoint na mesma transação do efeito. A
  violação da chave primária indica mensagem repetida.
- **DLQ**: `os-service.eventos.dlq` guarda a mensagem original e os cabeçalhos `x-death` do RabbitMQ para
  inspeção no painel do broker. O alerta **Falha ao processar mensagens da Saga** do New Relic conta os logs
  `falha_no_consumo`, `mensagem_sem_saga` e `mensagem_invalida`.

### 6.7 `TrabalhadorMensageria`

`app/infrastructure/trabalhador.py`. Uma thread daemon (`mensageria`) iniciada no `lifespan` da aplicação **só
quando `RABBITMQ_URL` está definida**; sem ela, a API atende e a Saga fica parada no outbox. Em cada conexão:

1. abre a conexão (`pika.BlockingConnection`), declara a topologia e liga `confirm_delivery` e
   `basic_qos(prefetch_count=10)`;
2. registra `tratar_mensagem` como consumidor de `os-service.eventos` e loga `mensageria_conectada`;
3. repete até a thread ser parada: processa eventos do broker (`process_data_events` por até 1 s), publica o
   outbox (`publicar_pendentes`) e, a cada `SAGA_VARREDURA_SEGUNDOS`, varre os prazos
   (`VerificarPrazosSagaUseCase`).

Se o broker ou o banco falhar, a thread não morre: loga `mensageria_reconectando` e tenta de novo, com espera
que dobra de 1 s até 30 s. Ao encerrar a aplicação, `parar()` sinaliza a thread e espera até 5 s.

O `lifespan` roda em cada processo uvicorn. Com `UVICORN_WORKERS=2` e 2 a 4 réplicas, há de 4 a 8
`TrabalhadorMensageria` consumindo a mesma fila (consumidores concorrentes). Isso é seguro: a Saga e a OS são
travadas com `FOR UPDATE`, o outbox e a varredura usam `SKIP LOCKED` e a inbox impede o efeito duplicado.

### 6.8 Propagação do trace

O agente New Relic cria o contexto do trace. Ao gravar no outbox, o serviço guarda os cabeçalhos do trace atual
(`insert_distributed_trace_headers`); o relay os publica como `headers` AMQP. No consumo, `consumo_rastreado`
abre uma transação de background por mensagem (nome = tipo do evento, grupo `Message/RabbitMQ`) e aceita os
cabeçalhos recebidos (`accept_distributed_trace_headers`). O trace que começa na requisição HTTP que abre a OS
continua pelas mensagens até a Execução e o Billing e volta. Compensações iniciadas pela varredura de prazos,
que não ocorrem dentro de uma transação, começam um trace novo. No log, o `correlation_id` das mensagens
consumidas é o `saga_id`.

---

## 7. Modelo de dados

PostgreSQL 16, banco `os`. O esquema completo (diagrama ER, colunas, tipos, índices, restrições, decisões de
modelagem e migrations) está em [`docs/modelo-de-dados.md`](docs/modelo-de-dados.md).

| Tabela | Conteúdo | Campos principais | Índices e restrições |
|---|---|---|---|
| `usuarios` | funcionários | `username`, `hashed_password` (bcrypt), `role` (`ADMIN` ou `ATENDENTE`), `ativo` | `username` único |
| `clientes` | clientes | `nome`, `cpf_cnpj` (só dígitos), `email`, `telefone`, `ativo` | `cpf_cnpj` único |
| `veiculos` | veículos | `placa`, `marca`, `modelo`, `ano`, `cliente_id` | `placa` única; índice em `cliente_id`; FK para `clientes` |
| `ordens_servico` | OS | `cliente_id`, `veiculo_id`, `status`, `valor_total`, `criado_em`, `iniciado_em`, `finalizado_em` | índices em `status` e `cliente_id`; FKs; `CHECK (valor_total >= 0)` |
| `itens_os` | itens da OS | `os_id`, `tipo`, `item_id`, `quantidade`, `descricao`, `preco_unitario` | índice em `os_id`; `CHECK (quantidade > 0)` e `CHECK (preco_unitario >= 0)`; `item_id` sem FK (catálogo da Execução) |
| `historico_status_os` | trajetória da OS | `os_id`, `status`, `entrou_em` | índice em `os_id` |
| `saga_instancia` | estado da Saga | `os_id`, `passo`, `estado`, `motivo`, `compensacoes`, `reserva_feita`, `orcamento_id`, `orcamento_ativo`, `pagamento_id`, `pagamento_confirmado`, `tentativas`, `expira_em` | `os_id` único; índices em `estado` e `expira_em` |
| `outbox` | mensagens a publicar | `exchange`, `rota`, `tipo`, `corpo`, `cabecalhos`, `criado_em`, `publicado_em` | índice em (`publicado_em`, `criado_em`) |
| `mensagens_processadas` | inbox | `message_id`, `tipo`, `processada_em` | `message_id` é a chave primária |

Enums nativos: `statusos`, `roleenum`, `tipoitem`, `passosaga` e `estadosaga`. Migrations em
`alembic/versions`: `0001` (usuários, clientes, veículos, OS, itens e histórico) e `0002` (Saga, outbox e
inbox). O `entrypoint.sh` roda `alembic upgrade head` a cada subida do container.

---

## 8. API REST

Dois tipos de token, ambos no cabeçalho `Authorization: Bearer <token>`:

| Quem | Onde obtém | O que acessa |
|---|---|---|
| Funcionário (`ADMIN` ou `ATENDENTE`) | `POST /auth/token` neste serviço | operação da oficina, conforme o papel |
| Cliente | `POST /auth/cpf`, na Lambda `tech-challenge-f4-auth` | as próprias ordens de serviço |

Os dois são assinados com a mesma `SECRET_KEY` (HS256); o claim `tipo` do JWT os separa. O token de
funcionário leva `sub` (username), `role`, `tipo = usuario` e `exp`; o de cliente leva `sub` (id do cliente),
`tipo = cliente` e `exp`. Token de cliente em rota de funcionário responde `403`, e o contrário também. A OS
de outro cliente responde `404`, idêntico a uma OS inexistente. A cada requisição o serviço confere se o
usuário ou o cliente do token existe e está ativo: desativar o cadastro corta o acesso sem esperar o token
expirar.

### Rotas

"Funcionário" é qualquer usuário ativo, `ADMIN` ou `ATENDENTE`.

| Método | Caminho | Perfil exigido | Descrição |
|---|---|---|---|
| POST | `/auth/token` | pública | login por formulário (`username`, `password`); devolve o JWT de funcionário. Limite de 10 req/min por IP em cada processo |
| POST | `/auth/usuarios` | `ADMIN` | cria usuário com papel `ADMIN` ou `ATENDENTE` |
| GET | `/auth/usuarios` | `ADMIN` | lista usuários (`skip`, `limit`) |
| DELETE | `/auth/usuarios/{usuario_id}` | `ADMIN` | desativa o usuário (não apaga a linha) |
| POST | `/cadastro/clientes` | funcionário | cadastra cliente; CPF ou CNPJ obrigatório, único e com dígitos verificadores válidos |
| GET | `/cadastro/clientes` | funcionário | lista clientes (`skip`, `limit`) |
| GET | `/cadastro/clientes/buscar?cpf_cnpj=` | funcionário | busca por CPF ou CNPJ, com ou sem máscara |
| GET | `/cadastro/clientes/{cliente_id}` | `ADMIN` | consulta cliente por id |
| PATCH | `/cadastro/clientes/{cliente_id}` | funcionário | atualiza `nome`, `email`, `telefone` e `ativo`; `cpf_cnpj` é imutável (422) |
| POST | `/cadastro/veiculos` | funcionário | cadastra veículo (`AAA-1234` ou Mercosul `AAA1A23`) |
| GET | `/cadastro/clientes/{cliente_id}/veiculos` | funcionário | veículos de um cliente |
| GET | `/cadastro/veiculos/{veiculo_id}` | funcionário | consulta veículo |
| PUT | `/cadastro/veiculos/{veiculo_id}` | funcionário | atualiza veículo |
| DELETE | `/cadastro/veiculos/{veiculo_id}` | funcionário | remove veículo (204) |
| POST | `/atendimento/os` | funcionário | abre a OS em `ABERTA` e inicia a Saga (201); exige cliente ativo, veículo do cliente e ao menos um serviço ou peça |
| GET | `/atendimento/os` | funcionário | lista OS (`skip`, `limit`, `status`) |
| GET | `/atendimento/os/metricas/tempo-medio` | `ADMIN` | tempo médio de execução e por status, em minutos |
| GET | `/atendimento/os/consulta` | cliente | as ordens de serviço do cliente do token |
| GET | `/atendimento/os/{os_id}` | funcionário, ou cliente dono da OS | detalhe da OS com `itens`, `historico` e `saga` |
| POST | `/atendimento/os/{os_id}/entregar` | funcionário | `FINALIZADA` para `ENTREGUE` |
| POST | `/atendimento/os/{os_id}/cancelar` | funcionário | cancelamento antes do pagamento; dispara as compensações |
| POST | `/interno/clientes/autenticar` | serviço interno (`X-Token-Interno`) | dados do cliente pelo CPF ou CNPJ, para a Lambda |
| GET | `/health` | pública | liveness: não toca o banco |
| GET | `/health/ready` | pública | readiness: confere o banco (`SELECT 1`) |

Corpo de `POST /atendimento/os`:

```json
{
  "cliente_id": "<uuid>",
  "veiculo_id": "<uuid>",
  "servicos": [{ "servico_id": "<uuid do catálogo da Execução>", "quantidade": 1 }],
  "pecas": [{ "peca_id": "<uuid do catálogo da Execução>", "quantidade": 2 }]
}
```

Os ids de serviço e peça são os do catálogo da Execução. Se o item não existir lá, a Execução responde
`ReservaRecusada` e a Saga cancela a OS.

Erros: o corpo é `{"detail": "...", "correlation_id": "..."}`; na validação do corpo pelo FastAPI (422), `detail` é uma lista com os campos inválidos. `404` para recurso inexistente, `409` para
conflito (CPF, placa ou username já cadastrado), `422` para regra de negócio violada ou corpo inválido, `401`
para token ausente, inválido ou expirado e `403` para perfil sem permissão. O cabeçalho `x-request-id` volta em
toda resposta.

### Rota interna da Lambda

`POST /interno/clientes/autenticar` recebe `{"cpf_cnpj": "<cpf ou cnpj>"}` e devolve
`{"id", "nome", "cpf_cnpj", "ativo"}`. É a rota que a Lambda de CPF usa para consultar o cliente sem ler o
banco do OS Service (ADR-022).

- Exige o cabeçalho **`X-Token-Interno`**, comparado com `TOKEN_INTERNO` por `hmac.compare_digest`. Sem o
  cabeçalho, com valor errado ou com `TOKEN_INTERNO` não configurada, a resposta é `403`: sem token
  configurado, a rota fica fechada.
- Devolve o cliente também quando ele está inativo (`ativo: false`): quem decide o acesso é a Lambda.
- `404` se o CPF ou CNPJ não existe; `422` se os dígitos verificadores não conferem.
- **Não tem rota no API Gateway.** A Lambda a alcança pelo NLB interno, dentro da VPC
  (`http://<dns do NLB>:80/interno/clientes/autenticar`). O token vem do SSM
  (`/tech-challenge-f4/segredos/token_interno`), o mesmo que o Secret do serviço usa.

```bash
curl -s -X POST http://localhost:8001/interno/clientes/autenticar \
  -H "X-Token-Interno: $TOKEN_INTERNO" -H "content-type: application/json" \
  -d '{"cpf_cnpj": "529.982.247-25"}'
```

### Rotas que não existem mais neste serviço

Passaram para a Execução (`/catalogo`, `/estoque`, fila de execução, início e fim do reparo) e para o Billing
(`/billing/orcamentos/{os_id}/aprovar` e `/rejeitar`, webhook de pagamento). Na OS, o status só muda pela Saga,
pela entrega e pelo cancelamento.

### Swagger e Postman

| Onde | URL |
|---|---|
| Local (docker compose) | `http://localhost:8000/docs` · `/redoc` · `/openapi.json` |
| Nuvem, pelo API Gateway | `<url_api>/docs` · `/redoc` · `/openapi.json` |

A `<url_api>` é o endpoint do API Gateway: `aws ssm get-parameter --name /tech-challenge-f4/apigateway/endpoint
--query Parameter.Value --output text`. O Swagger da Execução e o do Billing ficam em `/execucao/docs` e
`/billing/docs`, para não colidir com este (`PREFIXO_DOCS`). No Swagger, o botão **Authorize** oferece os dois
esquemas: `OAuth2PasswordBearer` (funcionário) e `TokenCliente`.

A jornada completa, com cadastro, caminho feliz, orçamento rejeitado, cancelamento de OS paga e regras de
acesso, está na coleção Postman `tech-challenge-f4-plataforma/docs/postman/oficina-fase04.postman_collection.json`,
com o ambiente `local.postman_environment.json` (OS Service em `http://localhost:8001`).

---

## 9. Configuração

Lida por `app/infrastructure/config.py` (pydantic-settings) a partir do ambiente e de um `.env` na raiz.

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `DATABASE_URL` | sim | sem padrão | conexão PostgreSQL; na nuvem vem do SSM `os-service/database_url` (`sslmode=require`) |
| `SECRET_KEY` | sim | sem padrão | chave de assinatura do JWT; **precisa ser idêntica à da Lambda**; na nuvem vem do SSM `segredos/jwt_secret_key` |
| `ALGORITHM` | não | `HS256` | algoritmo do JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | não | `60` | validade do token de funcionário |
| `ADMIN_USERNAME` | não | `admin` | usuário `ADMIN` semeado na primeira subida |
| `ADMIN_PASSWORD` | não | `admin123` | senha do usuário semeado; na nuvem vem do secret `ADMIN_PASSWORD` do GitHub ou de uma senha criada pelo pipeline |
| `ALLOWED_ORIGINS` | não | `["http://localhost:3000"]` | origens do CORS, lista JSON; no ConfigMap, `["*"]` |
| `LOG_FORMAT` | não | `console` | `json` em ambiente remoto (ConfigMap) |
| `LOG_LEVEL` | não | `INFO` | nível do logger raiz |
| `RABBITMQ_URL` | não | vazio | URL AMQP do broker; **vazia, a mensageria não sobe** e a Saga fica parada no outbox; na nuvem vem do SSM `rabbitmq/url` |
| `SAGA_PRAZO_RESPOSTA_SEGUNDOS` | não | `300` | prazo de resposta de cada passo da Saga e de cada compensação |
| `SAGA_PRAZO_APROVACAO_HORAS` | não | `24` | teto do prazo de aprovação do orçamento |
| `SAGA_VARREDURA_SEGUNDOS` | não | `30` | intervalo da varredura de prazos |
| `TOKEN_INTERNO` | não | vazio | token da rota `/interno`; vazio, a rota fica fechada; na nuvem vem do SSM `segredos/token_interno` |
| `UVICORN_WORKERS` | não | `2` | processos uvicorn (lido por `entrypoint.sh`); ConfigMap: `2` |
| `NEW_RELIC_LICENSE_KEY` | não | vazio | vazia, o agente não sobe; na nuvem vem do SSM `newrelic/license_key` e só entra no Secret se houver licença |
| `NEW_RELIC_APP_NAME` | não | `tech-challenge-oficina` no `.env.example` | nome da aplicação no New Relic; ConfigMap: `tech-challenge-f4-os-service` |
| `NEW_RELIC_APPLICATION_LOGGING_FORWARDING_ENABLED` | não | do agente | ConfigMap: `false`, porque o stdout já chega pelo `nri-bundle` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | só no compose | `oficina` | credenciais do container PostgreSQL do `docker compose` |

Onde cada valor é definido na nuvem: o **ConfigMap** `os-service` (`k8s/base/configmap.yaml`) traz
`ALLOWED_ORIGINS`, `LOG_FORMAT`, `LOG_LEVEL`, `UVICORN_WORKERS`, `NEW_RELIC_APP_NAME` e
`NEW_RELIC_APPLICATION_LOGGING_FORWARDING_ENABLED`; o **Secret** `os-service-segredos` (montado pelo pipeline a
cada deploy, nunca versionado) traz `DATABASE_URL`, `SECRET_KEY`, `RABBITMQ_URL`, `TOKEN_INTERNO`,
`ADMIN_PASSWORD` e, se houver licença, `NEW_RELIC_LICENSE_KEY`. O prazo de folga de 5 minutos da Saga não é
configurável.

O `.env.example` traz as variáveis do `docker compose` local. Ele não define `RABBITMQ_URL` nem `TOKEN_INTERNO`.

---

## 10. Como rodar localmente

Pré-requisitos: Docker com Compose, `make`, Python 3.12 (para testes e execução fora do container) e, para o
ambiente com mensageria, o repositório `tech-challenge-f4-plataforma` clonado ao lado deste.

Alvos do `Makefile` (`make help` lista todos):

| Alvo | O que faz |
|---|---|
| `make setup` | cria o `.env` a partir do `.env.example`, sem sobrescrever |
| `make up` | sobe a API e o PostgreSQL com hot-reload (`docker compose up --build`) |
| `make down` | derruba a stack local e remove os volumes |
| `make logs` | acompanha os logs da API |
| `make sh` | abre um shell no container da API |
| `make venv` | cria o `.venv` (Python 3.12) e instala `requirements.txt` |
| `make test` | roda os testes com SQLite em memória |
| `make test-docker` | roda os testes dentro do container, sem Python na máquina |
| `make cov` | testes com cobertura (`term-missing` e `coverage.xml`) |
| `make build` | constrói a imagem Docker `tech-challenge-f4-os-service:<commit>` |
| `make render` | renderiza os manifestos Kubernetes (`kubectl kustomize k8s/overlays/producao`) |
| `make implantar` | aciona o pipeline de deploy na `main` (`gh workflow run ci-cd.yml`) |
| `make conta`, `make infra-init`, `make infra-up`, `make infra-down`, `make infra-validate` | Terraform de `infra/` (seções 13 e 15) |

### Opção A · só a API REST, com `make up`

```bash
make up
```

Sobe a API em `http://localhost:8000` e o PostgreSQL em `localhost:5432`, aplica as migrations e semeia o
usuário `admin` / `admin123`. **Esta opção não inclui o RabbitMQ**: cadastro, login e abertura de OS funcionam,
mas a Saga fica parada no passo `RESERVANDO_PECAS`, com o comando `ReservarPecas` esperando no outbox.

| Recurso | URL |
|---|---|
| Swagger | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI | http://localhost:8000/openapi.json |
| Liveness | http://localhost:8000/health |
| Readiness | http://localhost:8000/health/ready |

### Opção B · com RabbitMQ e PostgreSQL do ambiente local da Saga

A plataforma sobe o RabbitMQ e os bancos dos serviços em docker compose (ADR-024):

```bash
cd ../tech-challenge-f4-plataforma
make local-up        # RabbitMQ, PostgreSQL do OS (5433) e da Execução (5434), MongoDB (27017)
```

| Componente | Endereço | Credenciais |
|---|---|---|
| RabbitMQ | `amqp://localhost:5672`, painel em http://localhost:15672 | `oficina` / `oficina` |
| PostgreSQL do OS Service | `localhost:5433`, banco `os` | `os` / `os` |

Depois, neste repositório, com o serviço rodando fora do container:

```bash
make venv
export DATABASE_URL="postgresql://os:os@localhost:5433/os"
export SECRET_KEY="troque-por-uma-chave-aleatoria-de-pelo-menos-32-chars"
export RABBITMQ_URL="amqp://oficina:oficina@localhost:5672/%2F"
export TOKEN_INTERNO="token-interno-local"
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --port 8001
```

Com `RABBITMQ_URL` definida, o log mostra `mensageria_conectada`, e o serviço declara as exchanges e a fila
`os-service.eventos`. A Execução e o Billing declaram as filas de comandos deles quando sobem (veja o README de
cada um). **Enquanto essas filas não existirem, o relay não consegue entregar os comandos**: registra
`publicacao_recusada` a cada volta e mantém as mensagens no outbox, que são publicadas assim que as filas
aparecerem.

O ambiente integrado (os três serviços, RabbitMQ e bancos) e a jornada completa pela coleção Postman estão
descritos no README da plataforma; o ambiente `local.postman_environment.json` espera o OS Service em
`http://localhost:8001`.

### Primeiro uso

```bash
BASE=http://localhost:8001     # ou http://localhost:8000 na opção A
campo() { python3 -c "import sys, json; print(json.load(sys.stdin)['$1'])"; }

TOKEN=$(curl -s -X POST $BASE/auth/token -d "username=admin&password=admin123" | campo access_token)
AUTH="Authorization: Bearer $TOKEN"; JSON="content-type: application/json"

CLIENTE=$(curl -s -X POST $BASE/cadastro/clientes -H "$AUTH" -H "$JSON" \
  -d '{"nome":"Ana Souza","cpf_cnpj":"529.982.247-25","telefone":"(11) 99999-0000","email":"ana@exemplo.com"}' | campo id)

VEICULO=$(curl -s -X POST $BASE/cadastro/veiculos -H "$AUTH" -H "$JSON" \
  -d "{\"placa\":\"ABC1D23\",\"marca\":\"Fiat\",\"modelo\":\"Uno\",\"ano\":\"2020\",\"cliente_id\":\"$CLIENTE\"}" | campo id)

# Com a Execução no ar, use o id de um serviço cadastrado no catálogo dela.
SERVICO=$(python3 -c 'import uuid; print(uuid.uuid4())')
OS=$(curl -s -X POST $BASE/atendimento/os -H "$AUTH" -H "$JSON" \
  -d "{\"cliente_id\":\"$CLIENTE\",\"veiculo_id\":\"$VEICULO\",\"servicos\":[{\"servico_id\":\"$SERVICO\",\"quantidade\":1}]}" | campo id)

curl -s $BASE/atendimento/os/$OS -H "$AUTH"      # status, historico e saga
```

O OS Service aceita qualquer UUID como id de serviço ou peça: quem valida é a Execução, que responde
`ReservaRecusada` (`ITEM_INEXISTENTE`) se o item não existir no catálogo. Para um item de verdade, cadastre-o no
catálogo da Execução (`POST /catalogo/servicos` e `POST /estoque/pecas`, no `tech-challenge-f4-execucao-service`).
Sem a Execução no ar, a OS abre normalmente e a Saga espera o prazo do passo (5 minutos) para cancelá-la.

---

## 11. Testes e cobertura

### Suítes

`python -m pytest --collect-only -q` coleta **273 testes**:

| Arquivo | Testes | O que cobre |
|---|---|---|
| `tests/test_dominio_puro.py` | 119 | agregado `OrdemDeServico` sem banco, HTTP nem ORM: todas as transições válidas e inválidas, precificação, avanço pela execução |
| `tests/test_saga_dominio.py` | 21 | `SagaAberturaOS` em memória: caminho feliz, cada falha e sua compensação, prazos, pagamento tardio, cancelamento |
| `tests/test_saga_fluxo.py` | 10 | a Saga pela API e pelos casos de uso: outbox, inbox, evento fora do passo, varredura de prazos, cancelamento |
| `tests/test_mensageria.py` | 9 | consumidor, relay e topologia com canal falso; e um teste contra RabbitMQ real |
| `tests/bdd/test_saga_abertura_os.py` | 6 | fluxo completo da Saga em Gherkin (abaixo) |
| `tests/test_atendimento.py` | 27 | OS pela API, escopo do cliente, entrega, rotas removidas, métricas, eventos de status |
| `tests/test_autenticacao_cliente.py` | 16 | token de cliente: tipo, expiração, chave, `sub` malformado, cliente desativado |
| `tests/test_auth.py` | 15 | login, usuários e papéis |
| `tests/test_cadastro.py` | 27 | clientes e veículos |
| `tests/test_interno.py` | 6 | rota interna e `X-Token-Interno` |
| `tests/test_observabilidade.py` | 13 | logs JSON, correlação, cabeçalhos de segurança, liveness e readiness |
| `tests/test_integridade.py` | 4 | restrições `CHECK` do banco |

Os testes usam SQLite em memória (`DATABASE_URL="sqlite:///:memory:"`). O teste `test_trabalhador_contra_rabbitmq_real`
só roda se houver um RabbitMQ em `localhost:5672` (usuário e senha `oficina`); sem ele é pulado. O job de testes
do CI sobe um RabbitMQ como serviço, então lá os 273 rodam.

```bash
make test         # suíte completa; cria o .venv na primeira vez
make cov          # com relatório de cobertura
make test-docker  # a mesma suíte dentro do container
```

Sem o `make`, com o `.venv` criado:

```bash
DATABASE_URL="sqlite:///:memory:" SECRET_KEY="chave-de-teste-com-mais-de-32-caracteres" \
  .venv/bin/python -m pytest -q
```

### BDD do fluxo completo da Saga

`tests/bdd/saga_abertura_os.feature` (Gherkin em português) com os passos em `tests/bdd/test_saga_abertura_os.py`
(`pytest-bdd`). O contexto é o mesmo em todos os cenários: um cliente com um veículo e uma OS aberta com 1
serviço e 2 unidades de uma peça.

| Cenário | O que prova |
|---|---|
| **Fluxo completo da OS, da abertura à entrega** | a Execução recebe `ReservarPecas`; responde `PecasReservadas` (150.00); o Billing recebe `GerarOrcamento`; `OrcamentoGerado` leva a OS a `AGUARDANDO_APROVACAO`; `OrcamentoAprovado` faz o Billing receber `Cobrar` e a OS ir a `AGUARDANDO_PAGAMENTO`; `CobrancaCriada` e `PagamentoConfirmado` fazem a Execução receber `EnfileirarOS`; `OSEnfileirada` leva a OS a `NA_FILA`; `DiagnosticoIniciado`, `ExecucaoIniciada` e `ExecucaoFinalizada` levam a OS a `FINALIZADA` com a Saga `CONCLUIDA`; a entrega leva a `ENTREGUE` |
| **Falha num passo desfaz os anteriores** (esquema com 4 exemplos) | `ReservaRecusada` termina em `CANCELADA` sem compensação; `OrcamentoRejeitado` envia `LiberarReserva` e termina em `NEGADA`; `PagamentoRecusado` envia `CancelarOrcamento` e `LiberarReserva` e termina em `CANCELADA`; `EnfileiramentoRecusado` envia `Reembolsar`, `CancelarOrcamento` e `LiberarReserva`, nessa ordem, e termina em `CANCELADA`. Em todos a Saga termina `COMPENSADA` |
| **Mensagem repetida pelo broker não repete o efeito** | entregar de novo o `PecasReservadas` não gera um segundo `GerarOrcamento` |

Como o BDD roda sem infraestrutura: a API é chamada pelo `TestClient`; o **relay** (`publicar_pendentes`) e o
**consumidor** (`tratar_mensagem`) são os reais, ligados a um **broker em memória** (`BrokerEmMemoria`) que
guarda o que o relay publica e entrega os eventos dos participantes simulados ao consumidor. As asserções leem a
OS e a Saga pela API (`GET /atendimento/os/{id}`).

```bash
DATABASE_URL="sqlite:///:memory:" SECRET_KEY="chave-de-teste-com-mais-de-32-caracteres" \
  .venv/bin/python -m pytest tests/bdd -v --cucumberjson=relatorio-bdd.json
```

No CI, o job de testes roda a suíte inteira com `--cucumberjson=relatorio-bdd.json` e publica o arquivo como o
artefato **`relatorio-bdd`** (Cucumber JSON) da execução. Para baixá-lo:
`gh run download <id da execução> --repo pesant0s/tech-challenge-f4-os-service -n relatorio-bdd`.

### Cobertura

- **Mínimo no CI: 80%.** O job de testes roda `pytest --cov=app --cov-fail-under=80`; abaixo disso o pipeline
  falha.
- **Execução do CI**: 273 testes passaram, cobertura total de 97,45%.
- **Execução local sem RabbitMQ**: 272 passaram e 1 foi pulado, cobertura de 95,18% (a diferença é o
  `trabalhador.py`, que só o teste contra o broker real exercita).
- **SonarQube Cloud** (projeto `pesant0s_tech-challenge-f4-os-service`, consulta pública a
  `https://sonarcloud.io/api/measures/component?component=pesant0s_tech-challenge-f4-os-service&metricKeys=coverage,bugs,vulnerabilities,code_smells`):

| Métrica | Valor |
|---|---|
| Cobertura | 97,4% |
| Bugs | 0 |
| Vulnerabilidades | 6 |
| Code smells | 1 |
| Quality Gate | aprovado |

As 6 vulnerabilidades apontadas são alertas de configuração, e não de código de aplicação: cinco no workflow
`ci-cd.yml` (`pip install` sem versões travadas, download do kubeconform sem `--proto`, e a senha do
PostgreSQL efêmero usado só no job de prova da imagem) e uma no `Dockerfile` (`COPY . .`). O code smell é a ordem
do `CORSMiddleware` em `app/main.py`. O escopo da análise é o repositório inteiro; `tests/**` e `alembic/**`
ficam fora da conta de cobertura (`sonar-project.properties`).

---

## 12. Pipeline CI/CD

Workflow único: `.github/workflows/ci-cd.yml`.

```mermaid
flowchart LR
  T["Testes e cobertura<br/>pytest · mínimo de 80%"] --> Q["Qualidade<br/>SonarQube Cloud"]
  M["Validar manifestos Kubernetes<br/>kustomize · kubeconform"]
  T --> I["Build e prova da imagem<br/>PostgreSQL e RabbitMQ"]
  M --> I
  I --> D0["Publicação e deploy não executados<br/>AMBIENTE_ATIVO diferente de true"]
  T --> D["Deploy no EKS<br/>AMBIENTE_ATIVO igual a true"]
  Q --> D
  M --> D
  I --> D
```

| Job | O que faz |
|---|---|
| **Testes e cobertura** | sobe um RabbitMQ 4.1 como serviço; `pip install -r requirements.txt`; `pytest --cov=app --cov-report=xml --cov-fail-under=80 --cucumberjson=relatorio-bdd.json`; publica os artefatos `cobertura` (`coverage.xml`) e `relatorio-bdd` |
| **Qualidade (SonarQube Cloud)** | depende dos testes; baixa o `coverage.xml` e roda o scan; o Quality Gate reprova o pipeline (`sonar.qualitygate.wait=true`) |
| **Validar manifestos Kubernetes** | `kubectl kustomize k8s/overlays/producao` e `kubeconform -strict` |
| **Build e prova da imagem** | depende dos testes e dos manifestos; constrói a imagem, sobe um PostgreSQL 16 (com senha de caracteres especiais) e um RabbitMQ, roda o container com o entrypoint real e espera até 60 s o `/health/ready` responder e o log `mensageria_conectada` aparecer. Dockerfile quebrado, migration inválida ou falha de conexão com o broker param aqui |
| **Publicação e deploy não executados** | na `main`, com `AMBIENTE_ATIVO` diferente de `true`: só escreve o motivo no resumo da execução |
| **Deploy no EKS** | na `main`, com `AMBIENTE_ATIVO` igual a `true`; depende de testes, qualidade, manifestos e imagem |

### Gatilhos e branches

- `push` e `pull_request` em `develop` e `main`, e `workflow_dispatch` (`make implantar`).
- Execuções do mesmo workflow e da mesma ref são serializadas; em Pull Request, a execução anterior é
  cancelada.
- A `main` é protegida: sem push direto, só entra código por Pull Request, e vale também para administradores.
  Os checks obrigatórios são **Testes e cobertura**, **Validar manifestos Kubernetes**, **Build e prova da
  imagem** e **Qualidade (SonarQube Cloud)**.

### `AMBIENTE_ATIVO`

O ambiente AWS é efêmero (ADR-024, na plataforma): só existe durante o ensaio e a gravação. A variável de
repositório `AMBIENTE_ATIVO` diz ao pipeline em que estado ele está; a plataforma a liga no `make up` e a
desliga no `make down`.

| Evento | `AMBIENTE_ATIVO` | O que roda |
|---|---|---|
| Pull Request, push em `develop` | qualquer | testes, qualidade, manifestos, build e prova da imagem |
| Push em `main` ou execução manual | `true` | tudo acima, mais o deploy no EKS |
| Push em `main` ou execução manual | ausente ou `false` | a parte sem AWS; o deploy é pulado, com o motivo no resumo |

Job pulado não é falha: é o comportamento esperado com o ambiente desligado. Já uma falha de autenticação na AWS
com a variável em `true` fica vermelha.

### Passos do deploy no EKS

1. Autentica na AWS (OIDC ou credenciais temporárias, abaixo) e lê o id da conta.
2. `terraform apply` de `infra/` (ECR e banco; idempotente: sem mudança, não altera nada), com o estado em
   `tech-challenge-f4-tfstate-<conta>`.
3. Login no ECR, `docker build` e `docker push` da imagem `tech-challenge-f4-os-service:<7 primeiros dígitos do commit>`.
4. `aws eks update-kubeconfig`, com o nome do cluster lido do SSM (`/tech-challenge-f4/cluster/nome`).
5. Cria o namespace `os-service`.
6. **Sincroniza o Secret** `os-service-segredos` com `kubectl create secret ... --dry-run | kubectl apply`, a
   partir do SSM (seção 9). A `ADMIN_PASSWORD` é o secret do GitHub, se existir; senão a que já está no cluster;
   senão uma senha nova de 24 caracteres. Sobrevive aos deploys porque o admin só é semeado na primeira subida.
7. `kustomize edit set image` troca o placeholder pela imagem do commit; `kubectl apply -k k8s/overlays/producao`;
   `kubectl rollout status` com limite de 300 s.
8. **Teste de fumaça** pelo API Gateway: `GET <endpoint>/health` até responder 200 (30 tentativas, a cada 10 s).
9. Em caso de falha, imprime `kubectl get all` e os últimos 100 logs.

### Autenticação na AWS e segredos do repositório

O pipeline escolhe sozinho: com o secret `AWS_ROLE_ARN`, assume a role por **OIDC** (sem chave estática); sem
ele, usa as **credenciais temporárias do AWS Academy** (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` e
`AWS_SESSION_TOKEN`), que expiram com o laboratório e são regravadas por `make github-segredos`, na plataforma.

| Configuração | Tipo | Para quê |
|---|---|---|
| `AMBIENTE_ATIVO` | variável | liga e desliga o deploy |
| `AWS_ROLE_ARN` | secret | autenticação por OIDC |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | secrets | credenciais temporárias do AWS Academy |
| `SONAR_TOKEN` | secret | análise no SonarQube Cloud |
| `ADMIN_PASSWORD` | secret | senha do usuário `admin` semeado no cluster |

As actions de terceiros do workflow estão fixadas por SHA do commit.

---

## 13. Infraestrutura própria (`infra/`)

Terraform (`>= 1.10`; providers `aws ~> 5.60` e `random ~> 3.6`) com o que é só deste serviço. A rede, o cluster
e o RabbitMQ pertencem à plataforma; aqui só se lê o que ela publicou no SSM.

| Recurso | Detalhe |
|---|---|
| `aws_ecr_repository` `tech-challenge-f4-os-service` | varredura de vulnerabilidades no push; `force_delete` para destruir sem sobras |
| `aws_ecr_lifecycle_policy` | mantém as 10 últimas imagens |
| `aws_db_instance` `tech-challenge-f4-os-service-db` | PostgreSQL 16, `db.t3.micro`, 20 GB `gp2`, criptografado, não público; banco `os`, usuário `os`; backup automático de 7 dias; sem snapshot final, sem proteção contra exclusão, sem enhanced monitoring nem Performance Insights |
| `aws_db_subnet_group` | subnets privadas da plataforma |
| `aws_security_group` e regra de entrada | porta 5432 aberta só para o security group do cluster |
| `random_password` | 24 caracteres, com `_` e `-` como únicos especiais |
| `aws_ssm_parameter` `os-service/database_url` | `SecureString` com a URL de conexão (`sslmode=require`) |

Saídas: `ecr_url` e `banco_endpoint`. Tags em todo recurso: `Project = tech-challenge-f4`, `Fase = 04`,
`Repositorio = tech-challenge-f4-os-service`, `ManagedBy = terraform`.

**Estado remoto.** Backend S3 no bucket `tech-challenge-f4-tfstate-<conta>` (criado pela plataforma com
`make bootstrap`), chave `os-service/terraform.tfstate`, criptografado e com bloqueio por arquivo
(`use_lockfile`). O bucket e a região entram por `-backend-config`, porque dependem da conta: o `make infra-init`
os informa.

**Parâmetros do SSM** (prefixo `/tech-challenge-f4/`, contrato em
`tech-challenge-f4-plataforma/docs/infraestrutura/contrato.md`):

| Parâmetro | Tipo | Quem usa |
|---|---|---|
| `rede/vpc_id` | lido pelo Terraform | security group do RDS |
| `rede/subnets_privadas` | lido pelo Terraform | subnet group do RDS |
| `cluster/sg_id` | lido pelo Terraform | regra de entrada do RDS |
| `os-service/database_url` | **escrito** pelo Terraform | lido pelo pipeline, vai para o Secret |
| `cluster/nome`, `apigateway/endpoint` | lidos pelo pipeline | `update-kubeconfig` e teste de fumaça |
| `segredos/jwt_secret_key`, `rabbitmq/url`, `segredos/token_interno`, `newrelic/license_key` | lidos pelo pipeline | Secret do serviço |

Alvos: `make conta` (mostra a conta AWS), `make infra-init`, `make infra-up` (aplica; a plataforma precisa estar
no ar), `make infra-down` (destrói) e `make infra-validate` (`terraform fmt -check`, `init -backend=false` e
`validate`, sem AWS). Todos os alvos de AWS exigem `AWS_PROFILE` ou as credenciais temporárias no ambiente.

---

## 14. Kubernetes (`k8s/`)

Kustomize com base compartilhada (`k8s/base`) e o overlay `producao` (`k8s/overlays/producao`: namespace
`os-service`, rótulo `app.kubernetes.io/instance: producao` e a imagem trocada pelo pipeline).

| Recurso | Configuração |
|---|---|
| Namespace | `os-service` |
| ServiceAccount | `os-service`, sem token montado no pod |
| Deployment | 2 réplicas; contêiner `api` na porta 8000 (`http`); `envFrom` do ConfigMap `os-service` e do Secret `os-service-segredos` |
| Probes | `startupProbe` em `/health` (5 s, até 12 falhas: cobre o `alembic upgrade head` do boot); `readinessProbe` em `/health/ready` (10 s); `livenessProbe` em `/health` (20 s) |
| Recursos | requests de 100m de CPU e 256Mi de memória; limite de 512Mi de memória |
| Segurança | `runAsNonRoot`, usuário e grupo 10001, sem escalada de privilégio, todas as capabilities removidas; o sistema de arquivos não é só leitura porque o agente do New Relic grava o próprio log |
| Service | `NodePort`; porta 80 para a porta `http`; **`nodePort` 30080**, fixo porque o NLB da plataforma aponta para ele |
| HPA | 2 a 4 réplicas, alvo de 70% de CPU; reduz com janela de estabilização de 300 s |
| PodDisruptionBudget | `minAvailable: 1` |
| ConfigMap | `ALLOWED_ORIGINS`, `LOG_FORMAT`, `LOG_LEVEL`, `UVICORN_WORKERS`, `NEW_RELIC_APP_NAME` e `NEW_RELIC_APPLICATION_LOGGING_FORWARDING_ENABLED` |

O Secret não está nos manifestos de propósito: quem o cria é o pipeline, a partir do SSM. Pelo mesmo motivo não
há deploy manual paralelo: ele divergiria do caminho que o pipeline garante. O HPA só mede CPU, porque a
memória de um processo Python é quase fixa e prenderia o HPA no máximo.

```bash
make render      # inspeciona o resultado antes de aplicar
make implantar   # aciona o pipeline de deploy na main, sem precisar de commit
```

**Imagem** (`Dockerfile`): build em dois estágios sobre `python:3.12-slim`; o estágio final roda como usuário
numérico 10001, tem `HEALTHCHECK` em `/health` e executa `entrypoint.sh`. O entrypoint faz `alembic upgrade
head` e sobe `uvicorn app.main:app` com `UVICORN_WORKERS` processos (padrão 2), prefixado por
`newrelic-admin run-program` quando `NEW_RELIC_LICENSE_KEY` está definida.

---

## 15. Deploy na AWS

Ordem de subida (contrato de infraestrutura da plataforma): **plataforma, depois os serviços, depois a Lambda**.
A destruição segue a ordem inversa.

```bash
# 1. Plataforma: rede, EKS, NLB, API Gateway, RabbitMQ, New Relic (cerca de 15 minutos)
cd tech-challenge-f4-plataforma
export AWS_PROFILE=<perfil>          # ou as credenciais temporárias do laboratório
make bootstrap                       # bucket do estado (uma vez por conta)
make up                              # liga AMBIENTE_ATIVO nos repositórios ao final
make github-segredos                 # grava as credenciais AWS e as chaves do New Relic nos repositórios

# 2. OS Service (a Execução e o Billing sobem em paralelo, do mesmo jeito)
cd ../tech-challenge-f4-os-service
make infra-up                        # ECR e RDS
make implantar                       # pipeline na main: imagem, Secret, manifestos, rollout e teste de fumaça

# 3. Lambda de CPF, depois que o OS Service estiver no ar
cd ../tech-challenge-f4-auth-lambda
make apply
```

O código precisa estar na `main` (por Pull Request) para o `make implantar` publicá-lo. Para conferir:

```bash
kubectl -n os-service get pods,hpa            # 2 pods Running
ENDPOINT=$(aws ssm get-parameter --name /tech-challenge-f4/apigateway/endpoint --query Parameter.Value --output text)
curl -s $ENDPOINT/health                      # {"status":"ok","version":"1.0.0"}
```

A senha do `admin` na nuvem é o secret `ADMIN_PASSWORD` do GitHub ou, se ele não existir, a senha criada pelo
pipeline:

```bash
kubectl -n os-service get secret os-service-segredos -o jsonpath='{.data.ADMIN_PASSWORD}' | base64 -d
```

**Destruição**, na ordem inversa:

```bash
cd tech-challenge-f4-auth-lambda && make destroy        # 1. Lambda
cd ../tech-challenge-f4-os-service && make infra-down   # 2. RDS e ECR do serviço (e dos outros serviços)
cd ../tech-challenge-f4-plataforma && make down         # 3. plataforma; desliga AMBIENTE_ATIVO antes de destruir
```

O `make down` da plataforma recusa enquanto existir Lambda ou banco dos serviços. O RDS não faz snapshot final.

---

## 16. Observabilidade

- **Agente New Relic.** Entra só quando `NEW_RELIC_LICENSE_KEY` está definida (o `entrypoint.sh` decide entre
  `newrelic-admin run-program uvicorn` e `uvicorn` puro). A mesma imagem roda igual em desenvolvimento, sem conta.
  A aplicação aparece como `tech-challenge-f4-os-service`.
- **Logs JSON** (`LOG_FORMAT=json`) no stdout, coletados pelo `nri-bundle` da plataforma. Cada linha leva
  `timestamp`, `level`, `logger`, `message`, `correlation_id` e, com o agente ativo, `trace.id` e `span.id`
  (do log se abre o trace, e vice-versa). Campos nulos ficam de fora do JSON.
- **Correlação.** Na requisição HTTP, o `correlation_id` é o `x-request-id` recebido (o API Gateway o define com
  o `requestId`) e volta ao cliente no cabeçalho `x-request-id` e em todo corpo de erro. Nas mensagens
  consumidas, o `correlation_id` é o `saga_id`.
- **`saga_id` e `os_id` em todo log da Saga.** Os eventos estruturados são:

| `evento` | Quando | Campos |
|---|---|---|
| `saga` | a cada passo da Saga (OS aberta, evento aplicado, prazo esgotado, cancelamento) | `saga_id`, `os_id`, `gatilho`, `passo`, `estado`, `motivo`, `comandos` |
| `os_status` | a cada entrada da OS em um status | `os_id`, `status_anterior`, `status_novo`, `segundos_no_status_anterior` |
| `mensagem_repetida`, `saga_evento_descartado` | evento repetido ou fora do passo | `tipo` |
| `mensagem_invalida`, `mensagem_sem_saga`, `falha_no_consumo` | mensagem para a DLQ ou devolvida à fila | `tipo` |
| `publicacao_recusada` | o broker não aceitou a mensagem do outbox | `rota` |
| `mensageria_conectada`, `mensageria_reconectando` | ciclo de vida da conexão com o broker | |

- **Traces pela fila.** O trace que nasce na requisição HTTP atravessa o RabbitMQ e continua na Execução e no
  Billing (seção 6.8). No New Relic, as transações de consumo aparecem como `OtherTransaction/Message/RabbitMQ/<tipo>`.
- **Healthchecks.** `/health` (liveness e startup) só confirma que o processo responde, sem tocar o banco: se
  dependesse do banco, uma indisponibilidade do RDS faria o Kubernetes matar pods saudáveis em cascata.
  `/health/ready` (readiness) confere o banco com `SELECT 1`.
- **Dashboard e alertas** são código Terraform da plataforma. O dashboard **Tech Challenge · Oficina** tem as
  páginas *Saga* (sagas iniciadas, concluídas, compensadas e com falha na compensação, motivos, passos por
  gatilho, mensagens repetidas ou descartadas, consumo por serviço, linha do tempo por `saga_id`), *Ordens de
  serviço* (volume diário e tempo médio por status), *API e integrações* e *Kubernetes*. Os alertas são:
  **Falha no processamento de ordens de serviço**, **Saga parada em falha na compensação**, **Falha ao processar
  mensagens da Saga** e **API fora do ar para o monitor externo**.

---

## 17. Decisões arquiteturais

Os ADRs ficam em `tech-challenge-f4-plataforma/docs/adr/`.

| ADR | Decisão | Relação com este serviço |
|---|---|---|
| ADR-017 | Três microsserviços, divididos pelas áreas da oficina | define o que é do OS Service (atendimento, cadastro, autenticação) e o que saiu dele |
| ADR-018 | Saga orquestrada, com o orquestrador no OS Service | seção 5 |
| ADR-019 | Mensageria com RabbitMQ, dentro do cluster | exchanges `oficina.comandos` e `oficina.eventos`, quorum queue com `x-delivery-limit` e DLQ |
| ADR-020 | Um banco por serviço: PostgreSQL na OS e na Execução, MongoDB no Billing | o banco `os` é só deste serviço |
| ADR-021 | Outbox transacional na publicação e consumo idempotente | `outbox` e `mensagens_processadas`; `ack` manual |
| ADR-022 | A Lambda de CPF consulta o OS Service, e não o banco dele | rota interna `/interno/clientes/autenticar` |
| ADR-023 | Cobrança pelo Mercado Pago com QR Code dinâmico, atrás de uma porta | o QR expirado chega como `PagamentoRecusado` (`PAGAMENTO_EXPIRADO`) |
| ADR-024 | Desenvolvimento local com docker compose, entrega na AWS | `AMBIENTE_ATIVO` e o ambiente efêmero |

Contrato de mensagens: `tech-challenge-f4-plataforma/docs/contratos/` (`asyncapi.yaml` e `README.md`).

---

## 18. Limitações conhecidas

- **Corrida das migrations com 2 réplicas.** Cada réplica roda `alembic upgrade head` na subida. Num banco
  vazio, as duas podem tentar criar o esquema ao mesmo tempo: uma delas pode reiniciar uma vez
  (`DuplicateTable alembic_version`) e na segunda tentativa sobe normalmente. Com o banco já migrado, não há
  efeito.
- **RabbitMQ no cluster usa `emptyDir`.** Se o pod do broker for recriado, as mensagens que ele já confirmou e
  ninguém consumiu se perdem. O outbox republica o que o broker não confirmou, e o prazo de cada passo da Saga
  (todos, menos `EM_EXECUCAO`, que não tem prazo) é a rede de segurança.
- **O relay para na primeira mensagem recusada.** Publica em ordem e interrompe a volta no primeiro comando sem
  fila de destino (por exemplo, o Billing ainda não declarou `billing-service.comandos`); comandos para outro
  serviço que estejam depois dele no outbox esperam a próxima volta. Cada tentativa recusada gera um log
  `publicacao_recusada`, cerca de uma por segundo por processo.
- **`FALHA_NA_COMPENSACAO` exige ação humana.** A API não tem rota para retomar uma Saga parada nesse estado.
- **A oficina só cancela antes do pagamento.** Depois de `PagamentoConfirmado` (passo `ENFILEIRANDO` em diante) o
  `POST .../cancelar` responde 422.
- **`outbox` e `mensagens_processadas` não têm limpeza.** As linhas ficam no banco depois de publicadas ou
  processadas.
- **Rate limit por processo.** O limite de 10 req/min em `POST /auth/token` é em memória, por IP e por processo:
  com 2 processos por réplica e 2 a 4 réplicas, o limite efetivo é maior.
- **`marca` e `modelo` do veículo.** A API aceita até 100 caracteres, mas as colunas guardam 60; com mais de 60 o
  PostgreSQL recusa e a API responde 500.
- **Remover veículo que tem OS.** O banco recusa (chave estrangeira) e a API responde 500, e não 409.
- **Coleção Postman antiga.** `postman/oficina.postman_collection.json` é anterior à divisão em serviços e chama
  rotas que não existem mais neste serviço (`/aprovar`, `PATCH .../status`, `/catalogo`, `/estoque`). A coleção
  vigente está na plataforma.
- **`docker compose` local sem broker.** `make up` não sobe o RabbitMQ e o `.env.example` não define
  `RABBITMQ_URL` nem `TOKEN_INTERNO` (seção 10).

---

## 19. Estrutura do repositório

```
.
├── app/                         código do serviço (seção 2)
├── alembic/                     migrations (0001 e 0002) e env.py
├── docs/modelo-de-dados.md      esquema completo do banco
├── infra/                       Terraform: ECR, RDS, parâmetro do SSM
├── k8s/
│   ├── base/                    deployment, service, hpa, pdb, configmap, serviceaccount, namespace
│   └── overlays/producao/       kustomization do ambiente
├── tests/                       suítes pytest e tests/bdd (Gherkin)
├── postman/                     coleção anterior à divisão em serviços (seção 18)
├── .github/workflows/ci-cd.yml  pipeline
├── Dockerfile · entrypoint.sh · docker-compose.yml · .env.example
├── Makefile · requirements.txt · alembic.ini · sonar-project.properties
```
