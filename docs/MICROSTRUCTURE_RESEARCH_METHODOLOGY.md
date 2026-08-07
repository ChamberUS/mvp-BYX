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
| USD-M Futures | `wss://fstream.binance.com/stream` | `ethusdt@aggTrade`, `ethusdt@bookTicker`, `ethusdt@depth@100ms`, `ethusdt@markPrice@1s` | `GET /fapi/v1/depth` |

Somente dados públicos são persistidos. O payload original recebe representação JSON canônica
lossless e SHA-256. Para `aggTrade`, `buyer_is_maker=false` significa agressor comprador e
`buyer_is_maker=true`, agressor vendedor.

## Eventos, relógios e armazenamento

`MicrostructureEvent` é imutável e separa horário do evento e da transação na exchange, horário
de recebimento na parede e contador monotônico local. Os tipos são `AGG_TRADE`, `BOOK_TICKER`,
`DEPTH_UPDATE`, `MARK_PRICE`, `SNAPSHOT` e `CONNECTION_STATE`. Preços e quantidades usam
`Decimal`.

Arquivos append-only `JSONL gzip` são particionados por mercado, símbolo, data UTC e sessão. A
rotação não cria uma transação SQLite por tick e não muda o schema v4. O manifest registra
contagens, primeiro/último evento, SHA-256 por arquivo, tamanho bruto/comprimido, gaps,
disconnects, resyncs e completude. Restos `.part` de uma interrupção podem ser validados e
recuperados sem transformar uma sessão incompleta em completa.

`MicrostructureReplayEngine` valida hashes, ordena por event time, sequence, receive monotonic e
ID estável. `VirtualClock` nunca retrocede. Os modos `1x`, `max` e `step` preservam as mesmas
decisões e não usam `sleep` real para timers estratégicos; a velocidade é uma política do
consumidor, não uma fonte de tempo para o alpha.

## Livro local e integridade

Cada `LocalOrderBook` pertence a um único par mercado/símbolo. O bootstrap abre o stream,
bufferiza diff updates, obtém o snapshot REST, descarta updates cobertos por `lastUpdateId` e
exige que o primeiro update aplicável contenha `lastUpdateId + 1`. Spot valida `U/u`; Futures
também valida o elo `pu`. Updates duplicados e antigos não são reaplicados. Jump, out-of-order
incompatível, gap ou livro cruzado tornam o livro `INVALID`, produzem `ORDER_BOOK_DESYNC`,
bloqueiam alpha e exigem resync com novo snapshot.

A conexão responde a ping/pong, tem timeout, limite de frame, reconexões limitadas e backoff
exponencial limitado. Contadores separam conexões, reconnects, snapshots, sequence gaps, resyncs
e downtime. Não existe retry infinito.

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
  --output-dir data/microstructure --duration-seconds 60
adaptive-trader market microstructure record --market futures --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth,markPrice --depth-speed 100ms \
  --output-dir data/microstructure --duration-seconds 60
adaptive-trader market microstructure inspect --session <session>
adaptive-trader research microstructure replay --session <session> --speed max \
  --output-dir reports/research
adaptive-trader research microstructure alpha-diagnose --session <session> \
  --models long,short --output-dir reports/research
```

O replay cria exatamente os 11 artefatos da Sprint 4A.1 com integridade da captura/livro,
liquidez, features, alpha, `NO_TRADE`, contrato Elastic sintético e determinismo. Não cria
candidate assessment financeiro.

## Limites

Isto não é HFT institucional. Python, o scheduler do sistema, TLS, Internet pública, relógios
locais, frames agregados de 100 ms e ausência de colocação em fila impedem garantias de latência
submilissegundo. O livro é uma reconstrução de feeds públicos, não uma visão co-localizada. Smoke
captures curtas validam engenharia e não desempenho financeiro. Custos, fills parciais, impacto
e adverse selection ainda precisam de simulador e validação pré-registrada antes de qualquer
conclusão econômica.
