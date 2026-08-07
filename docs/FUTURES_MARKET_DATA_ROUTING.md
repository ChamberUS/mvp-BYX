# Roteamento de market data USD-M Futures

## Fonte e versão observada

Esta implementação foi conferida em `2026-08-07` exclusivamente contra a documentação oficial
Binance USD-M Futures:

- `Websocket Market Streams / Connect`;
- `Important WebSocket Change Notice — Base URL Split & Migration`;
- `How to manage a local order book correctly`.

As páginas oficiais indicavam última modificação em `2026-08-06`. A mudança separa o host raiz
em rotas `/public`, `/market` e `/private`; a URL legada sem rota teve desativação documentada
para `2026-04-23`. Este projeto nunca usa `/private`.

## Manifesto de subscrição

| Stream solicitado | Stream canônico | Rota | URL combinada |
| --- | --- | --- | --- |
| `bookTicker` | `<symbol>@bookTicker` | `PUBLIC` | `wss://fstream.binance.com/public/stream?streams=...` |
| `depth` | `<symbol>@depth@100ms` | `PUBLIC` | `wss://fstream.binance.com/public/stream?streams=...` |
| `aggTrade` | `<symbol>@aggTrade` | `MARKET` | `wss://fstream.binance.com/market/stream?streams=...` |
| `markPrice` | `<symbol>@markPrice@1s` | `MARKET` | `wss://fstream.binance.com/market/stream?streams=...` |

Símbolos de stream são sempre minúsculos. `FuturesStreamRouter` resolve a rota, monta a URL e
valida a conexão antes de abri-la. Duplicidade, stream desconhecido, rota incompatível, URL
legada, private stream e listen key falham de forma explícita.

PUBLIC e MARKET têm connection IDs e sequências locais independentes. Cada evento persiste:

- exchange event time;
- exchange transaction time quando o payload possui uma transação;
- receive wall time e receive monotonic;
- connection ID e sequência crescente naquela conexão;
- payload canônico e SHA-256.

O merge/replay usa, nesta ordem: exchange event time, exchange transaction time (ou event time),
connection ID, connection sequence, receive monotonic e event ID. Isso evita depender da ordem
fortuita em que duas tasks entregam mensagens.

## Liveness por stream

O ciclo é `REQUESTED -> CONNECTED -> WAITING_FIRST_EVENT -> LIVE`. Timeout do primeiro evento
leva a `FAILED`; ausência além do limite leva a `STALE`; novo evento válido recupera `LIVE` e
incrementa recovery. Reinício de socket volta a `REQUESTED` com reason
`CONNECTION_RESTART`.

O update speed anunciado não é tratado como heartbeat obrigatório. `depth@100ms` tem janela de
observação após 2 s e escala somente após 10 s com evidência cruzada; `bookTicker` é
change-driven, com 10/30 s; `aggTrade` é execution-driven, com 30/60 s; e `markPrice@1s`, a
única stream aproximadamente periódica, usa 2,5/5 s. Todos os valores são
`ENGINEERING_ASSUMPTION`, não thresholds ajustados ao resultado da captura.

Um silêncio abre `DETECTED`, passa por `OBSERVING` e termina em `RECOVERED`, `UNRESOLVED` ou
`ESCALATED`. Atividade de bookTicker/aggTrade, socket/ping, fila local e a continuidade do `pu`
seguinte são evidências separadas. Um incidente recuperado preserva o aviso da sessão, porém o
estado atual volta a `READY`; apenas falha ativa ou integridade perdida impede readiness.

## Livro local USD-M

O bootstrap oficial é:

1. abrir `.../public/stream?streams=<symbol>@depth@100ms`;
2. bufferizar updates durante o request REST;
3. obter `GET https://fapi.binance.com/fapi/v1/depth?symbol=<SYMBOL>&limit=1000`;
4. descartar evento com `u < lastUpdateId`;
5. aceitar como primeiro apenas `U <= lastUpdateId <= u`;
6. para todos os eventos seguintes, exigir `event.pu == previous_event.u`.

A condição Spot que procura `previous + 1` dentro de `U/u` não participa do steady state
Futures. Aplicá-la ali gera falsos gaps mesmo quando `pu` está encadeado corretamente.

As classificações persistidas são `REAL_SEQUENCE_GAP`, `SNAPSHOT_ALIGNMENT_RETRY`, `OLD_EVENT`,
`DUPLICATE_EVENT`, `OUT_OF_ORDER_EVENT`, `STALE_EVENT`, `CONNECTION_RESTART` e `PARSER_ERROR`.
Um gap real invalida o livro, bloqueia consumidores e inicia novo snapshot; retry de alinhamento
é contabilizado separadamente.

## Payloads complementares

`markPrice` preserva mark price (`p`), index price (`i`), funding rate (`r`) e next funding time
(`T`). Nesse payload, `T` não é transaction time. `aggTrade` preserva aggregate trade ID (`a`),
first trade ID (`f`) e last trade ID (`l`). `bookTicker` é comparado ao best bid/ask do livro
reconstruído e o delta é escrito em `book_ticker_alignment.csv`.

## Gates e execução operacional

```bash
adaptive-trader market microstructure record --market futures --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth,markPrice --depth-speed 100ms \
  --duration 300 --output-dir data/microstructure

adaptive-trader market microstructure health --session <session>

adaptive-trader research microstructure futures-feed-harden \
  --session <session> --previous-session <previous-30s-session> \
  --output-dir reports/research

adaptive-trader research microstructure futures-liveness-qualify \
  --session <qualification-300s-session> --previous-session <previous-300s-session> \
  --long-session <long-1800s-session> --output-dir reports/research
```

O smoke é válido somente com os quatro streams entregues e parseados, hashes/completude válidos,
livro sincronizado, zero parser error, zero gap real, conexões estáveis e replay idêntico duas
vezes. Apenas `READY_FOR_LONG_CAPTURE` permite:

```bash
adaptive-trader market microstructure record --market futures --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth,markPrice --depth-speed 100ms \
  --duration 1800 --output-dir data/microstructure
```

`NOT_READY_FOR_LONG_CAPTURE` é terminal para a etapa: não há alpha, ordem, Testnet, paper
trading ou tentativa
privada para contornar falha de dados.
