# Metodologia de pesquisa de microestrutura intraday

## Escopo e objetivo

A Sprint 4A.1 muda o objetivo principal para pesquisa de trading intraday sistemático de alta
rotatividade relativa, com horizonte de segundos a minutos, no máximo uma posição e alavancagem
travada em `1x`. A faixa de 5–20 operações fechadas em um dia ativo é apenas diagnóstico: não é
quota, não altera thresholds e nunca justifica criar um sinal. `NO_TRADE` é resultado de primeira
classe.

Esta fundação não autentica, não aceita API key, não abre listen key, não chama endpoints de
conta, não envia ordens, não inicia Testnet ou paper trading e não declara lucratividade. As
estratégias anteriores permanecem benchmarks históricos sem alteração retroativa.

## Fontes públicas documentadas

Os nomes e endpoints foram conferidos na documentação oficial atual da Binance antes da
implementação. Para `ETHUSDT`, a captura combinada usa:

| Mercado | WebSocket base | Streams | Snapshot público |
| --- | --- | --- | --- |
| Spot | `wss://stream.binance.com:9443/stream` | `ethusdt@aggTrade`, `ethusdt@bookTicker`, `ethusdt@depth@100ms` | `GET /api/v3/depth` |
| USD-M Futures PUBLIC | `wss://fstream.binance.com/public/stream?streams=...` | `ethusdt@bookTicker`, `ethusdt@depth@100ms` | `GET /fapi/v1/depth` |
| USD-M Futures MARKET | `wss://fstream.binance.com/market/stream?streams=...` | `ethusdt@aggTrade`, `ethusdt@markPrice@1s` | — |

O mapeamento USD-M foi observado na documentação oficial em `2026-08-07`. O roteador tipado
rejeita `/private`, listen keys, URL legada sem rota e qualquer stream no agrupamento errado.
PUBLIC e MARKET usam sockets independentes. O merge persistido desempata por event time,
transaction time, connection ID, sequência monotônica da conexão, receive monotonic e event ID.
Detalhes e fontes oficiais estão em `docs/FUTURES_MARKET_DATA_ROUTING.md`.

Somente dados públicos são persistidos. O payload original recebe representação JSON canônica
lossless e SHA-256. Para `aggTrade`, `buyer_is_maker=false` significa agressor comprador e
`buyer_is_maker=true`, agressor vendedor.

## Eventos, relógios e armazenamento

`MicrostructureEvent` é imutável e separa horário do evento e da transação na exchange, horário
de recebimento na parede, contador monotônico local, connection ID e sequência por conexão. Os tipos são `AGG_TRADE`, `BOOK_TICKER`,
`DEPTH_UPDATE`, `MARK_PRICE`, `SNAPSHOT` e `CONNECTION_STATE`. Preços e quantidades usam
`Decimal`.

Arquivos append-only `JSONL gzip` são particionados por mercado, símbolo, data UTC e sessão. A
rotação não cria uma transação SQLite por tick e não muda o schema v4. O manifest registra
contagens, primeiro/último evento, SHA-256 por arquivo, tamanho bruto/comprimido, gaps,
disconnects, resyncs e completude. Restos `.part` de uma interrupção podem ser validados e
recuperados sem transformar uma sessão incompleta em completa.

`MicrostructureReplayEngine` valida hashes e aplica a mesma chave de merge cross-connection.
`VirtualClock` nunca retrocede. Os modos `1x`, `max` e `step` preservam as mesmas
decisões e não usam `sleep` real para timers estratégicos; a velocidade é uma política do
consumidor, não uma fonte de tempo para o alpha.

## Livro local e integridade

Cada `LocalOrderBook` pertence a um único par mercado/símbolo. As políticas não são
intercambiáveis. Spot exige a continuidade documentada via `U/u` contendo o update anterior
mais um. USD-M Futures descarta apenas eventos com `u < lastUpdateId`, alinha o primeiro evento
por `U <= lastUpdateId <= u` e, daí em diante, exige exclusivamente
`event.pu == previous_event.u`. A regra Spot de `previous + 1` não é aplicada após o bootstrap
Futures.

Falhas recebem uma classificação explícita: `REAL_SEQUENCE_GAP`,
`SNAPSHOT_ALIGNMENT_RETRY`, `OLD_EVENT`, `DUPLICATE_EVENT`, `OUT_OF_ORDER_EVENT`,
`STALE_EVENT`, `CONNECTION_RESTART` ou `PARSER_ERROR`. Somente gaps reais incrementam o contador
de sequence gap; retry de alinhamento pede novo snapshot sem transformar um artefato de
bootstrap em gap da exchange. Todo resync registra sequências anterior/observada, conexão,
horário, resultado e classificação.

A conexão responde a ping/pong, tem timeout, limite de frame, reconexões limitadas e backoff
exponencial limitado. `StreamLivenessMonitor` acompanha cada stream em `REQUESTED`, `CONNECTED`,
`WAITING_FIRST_EVENT`, `LIVE`, `STALE` ou `FAILED`, com cadência/timeout próprio e recuperação
registrada. Contadores separam conexões, reconnects, snapshots, sequence gaps, resyncs e
downtime. Não existe retry infinito.

`MicrostructureFeedHealth` decide `READY`, `DEGRADED` ou `NOT_READY`. O scorecard decide
`CAPTURE_VALID`, `CAPTURE_VALID_WITH_WARNINGS` ou `CAPTURE_INVALID`. Em Futures, ausência de
qualquer um dos quatro streams, parse inválido, hash/completude inválidos ou livro sem sync
produz `NOT_READY`; `alpha-diagnose` recusa esse input.

## Liquidez e features point-in-time

`LiquiditySnapshot` contém bid/ask, mid, spread em preço e bps, notional e imbalance nos top
5/10/20, idade e estado de sincronização. Ela estima VWAP executável, slippage e notional visível
dentro de 1/2/5 bps. Entrada long consome asks; entrada short consome bids. Para encerrar long, o
preço conservador consome bids; para encerrar short, consome asks. Volume de candle não substitui
depth.

O microprice usa as quantidades do melhor nível:

```text
microprice = (best_ask * bid_qty + best_bid * ask_qty) / (bid_qty + ask_qty)
```

Depth imbalance usa `(bid_depth - ask_depth) / (bid_depth + ask_depth)`. Aggressive flow cobre
250 ms, 1 s, 3 s e 10 s. OFI usa contribuições de preço/quantidade do best bid menos as do best
ask em 250 ms, 1 s e 3 s. Momentum do mid cobre 250 ms, 1 s, 3 s e 10 s; movimento realizado
quadrático cobre 1 s, 5 s e 30 s. Toda consulta filtra o prefixo `timestamp <= now`, inclusive
nas bordas, e registra event/book/trade age.

## NO_TRADE, long e short

`NoTradeGate` bloqueia livro sem sync, stale data, gap recente, resync, spread inválido, depth
insuficiente, warmup, dados incompletos, replay divergente ou estado desconhecido. Long e short
são classes, configurações, reason codes e estados independentes. Short é permitido somente em
USD-M Futures; não existe short Spot. Confirmação simultânea produz `NO_TRADE_CONFLICT`.

Os thresholds V0 são `CALIBRATION_REQUIRED`. A fundação registra sinais por minuto/hora/dia,
percentual de `NO_TRADE` e persistência média/mediana, mas não usa frequência ou PnL para ajustar
limiares. Os gates futuros classificam liquidez como `LIQUIDITY_OK`, `LIQUIDITY_THIN` ou
`LIQUIDITY_UNSAFE` usando quantidade, VWAP, slippage e fração do depth visível.

Alpha produz somente `IntradayAlphaDecision`. Os protocolos `PortfolioRiskGovernor` e
`ExecutionPlanner` e o domínio `IntradayOrderIntent` mantêm risco e execução fora do modelo. O
contrato `IntradayRiskConfig` valida perdas, cooldown, frequência, notional, participação no
depth, slippage, kill switch, uma posição e leverage exatamente `1x`; não há implementação de
exchange.

## Elastic Profit Exit experimental

`ELASTIC_300_150_V0` é hipótese sintética não selecionada. Ela arma somente quando o lucro
líquido estimado no preço executável supera a ativação após fees e slippage. Para long, o
controller vende contra bids; para short, recompra contra asks. Mark price continua apropriado
para margem, manutenção e liquidação Futures, mas nunca representa realização de lucro.

Um novo pico favorável reinicia o prazo de 300 ms. Sem novo pico por 300 ms, solicita saída. Uma
reversão microestrutural persistente inicia `REVERSAL_PENDING` e solicita saída aos 150 ms;
recuperação anterior cancela o estado. O hard floor por lucro mínimo/retração máxima tem
prioridade imediata. Spread excessivo, livro desincronizado, depth insuficiente ou dado stale
acionam `LIQUIDITY_EXIT_FAILSAFE`. Tudo depende de timestamps de evento, sem sleep bloqueante.

A infraestrutura de markout mede 100/250/500 ms e 1/3/5/15/60 s após sinal/fill sintético,
separando long/short, maker/taker e mid/preço executável. Essas medidas são
`POST_EVENT_ONLY` e nunca entram retroativamente no alpha.

## CLI e relatório reprodutível

```bash
adaptive-trader market microstructure doctor
adaptive-trader market microstructure record --market spot --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth --depth-speed 100ms \
  --output-dir data/microstructure --duration 60
adaptive-trader market microstructure record --market futures --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth,markPrice --depth-speed 100ms \
  --output-dir data/microstructure --duration 3600
adaptive-trader market microstructure inspect --session <session>
adaptive-trader market microstructure health --session <session>
adaptive-trader research microstructure replay --session <session> --speed max \
  --output-dir reports/research
adaptive-trader research microstructure alpha-diagnose --session <session> \
  --models long,short --output-dir reports/research
adaptive-trader research microstructure futures-feed-harden --session <session> \
  --previous-session <previous-30s-session> --output-dir reports/research
adaptive-trader research microstructure futures-liveness-qualify --session <session-300s> \
  --previous-session <previous-300s-session> --long-session <session-1800s> \
  --output-dir reports/research
```

O replay cria exatamente os 11 artefatos da Sprint 4A.1 com integridade da captura/livro,
liquidez, features, alpha, `NO_TRADE`, contrato Elastic sintético e determinismo. Não cria
candidate assessment financeiro.

O hardening Futures cria exatamente 13 artefatos de transporte, liveness, sequência,
alinhamento bookTicker, resync, qualidade e replay. Uma captura curta nunca recebe
`READY_FOR_LONG_CAPTURE`: primeiro é obrigatório validar 300 segundos e todos os quatro streams;
somente então a captura de 1.800 segundos pode ser iniciada.

A qualificação 4A.2.2 não equipara update speed a heartbeat. Ela separa receive cadence de
exchange cadence, transport latency de processamento local, e `DEPTH_SILENCE` de
`DEPTH_SEQUENCE_GAP`. Current health pode voltar a `READY` após um incidente `RECOVERED`, enquanto
a session quality preserva `VALID_WITH_WARNINGS`. Fila limitada, drops, backlog, event-loop,
consistência cross-stream e cinco hashes do replay participam do gate objetivo descrito em
`docs/MICROSTRUCTURE_LIVENESS_AND_QUALITY.md`.

`--duration 0` mantém a captura até SIGINT/SIGTERM e ainda fecha o arquivo corrente e o manifest.
`--duration-seconds` permanece como alias compatível. A camada posterior de execução, incluindo
arrival time, fila aproximada, partial fill, cancel race, fees, PnL executável e determinismo,
está documentada em `docs/INTRADAY_EXECUTION_SIMULATION.md`.

## Limites

Isto não é HFT institucional. Python, o scheduler do sistema, TLS, Internet pública, relógios
locais e frames agregados de 100 ms impedem garantias de latência submilissegundo. O livro é uma
reconstrução pública, não uma visão co-localizada. A Sprint 4A.2 adiciona um simulador mecânico,
mas posição de fila continua sendo aproximação conservadora e impacto além do depth visível não
é inventado. Smoke captures e cenários sintéticos validam engenharia, não desempenho financeiro;
qualquer conclusão econômica ainda exige pesquisa de alpha e validação pré-registrada posterior.

## Sprint 4A.3: features não são labels

Campaigns, anchors de 250 ms, labels executáveis LONG/SHORT, tiers 100/500/1.000, horizontes
250 ms–60 s, split temporal bloqueado e bootstrap em blocos estão especificados em
`docs/INTRADAY_EDGE_DISCOVERY.md`. Features usam somente o prefixo até `T`; forward return, MFE e
MAE existem apenas no módulo/arquivo offline de labels. Alpha não importa esse módulo, confirmation
não recalcula quantis e discovery rejeita `LOCKED_FUTURE_HOLDOUT`.

O resultado atual é somente `ENGINEERING_ONLY/MORE_DATA_REQUIRED`. A diferença
`receive_wall - exchange_event_time` continua excluída de features, labels e conclusões porque os
relógios não estavam alinhados.

## Dados consumidos e episódios multi-minute

Após produzir hipótese, o campaign de 30 minutos tornou-se `ENGINEERING_CONSUMED`; seu hash não
pode reaparecer na seleção multi-day. O novo protocolo estende availability labels até 15 minutos,
mas rejeita qualquer horizon que cruze session end/CAPTURE_BREAK. Para evitar pseudo-replicação,
episodes são não sobrepostos por lado, notional, política e variante de saída. Bootstrap futuro usa
blocos temporais de 30 minutos. Consulte `docs/MULTI_DAY_EXECUTION_ECONOMICS.md`.

## Proveniência obrigatória para datasets científicos

Cada nova sessão persiste SHA completo, `dirty_worktree`, branch, versão do recorder e hash canônico
da configuração. Falha ao consultar Git não impede gravar raw, mas produz
`PROVENANCE_INCOMPLETE`; sessão dirty ou UNKNOWN não entra em discovery/confirmation. Admissão
também verifica quatro streams, integridade, book sincronizado e replay determinístico. Breaks não
são interpolados e chunks rejeitados não contaminam chunks válidos. O contrato completo e a
resposta central estão em `docs/MULTI_DAY_ECONOMIC_QUALIFICATION.md`.
