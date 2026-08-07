# Liveness e qualidade da microestrutura USD-M Futures

## Escopo e fontes

A Sprint 4A.2.2 qualifica somente dados públicos. A implementação foi reconferida em
`2026-08-07` com a documentação oficial Binance USD-M Futures de conexão, separação das rotas
`/public` e `/market` e reconstrução do livro local. Ela responde pings do servidor e observa o
pong enviado, sem autenticação, listen key, `/private`, Testnet ou ordens.

As rotas são `bookTicker` e `depth@100ms` em `/public`; `aggTrade` e `markPrice@1s` em
`/market`. O livro alinha por `U <= lastUpdateId <= u` e, depois, exige
`event.pu == previous_event.u`.

## Update speed não é heartbeat

`100ms` e `1s` descrevem frequências de atualização do produto, mas não garantem um payload em
cada intervalo quando nada mudou. A política reflete a natureza de cada stream:

| Stream | Natureza | Observação | Escalação |
| --- | --- | ---: | ---: |
| `depth@100ms` | mudança do livro | 2.000 ms | 10.000 ms |
| `bookTicker` | mudança do best bid/ask | 10.000 ms | 30.000 ms |
| `aggTrade` | execução de negócios | 30.000 ms | 60.000 ms |
| `markPrice@1s` | aproximadamente periódica | 2.500 ms | 5.000 ms |

Esses budgets são `ENGINEERING_ASSUMPTION`. Para depth, silêncio e sequence gap são eventos
distintos. bookTicker/aggTrade ativos elevam a necessidade de observação, mas somente quebra do
`pu`, resync ou livro inválido comprova perda de integridade.

## Tempos e saúde do recorder

Exchange event time mede a cadência publicada. Receive monotonic mede inter-arrival local.
`receive_wall - exchange_event_time` é latência aparente de transporte e inclui offset entre
relógios. Ela não é latência de estratégia, ordem nem event loop.

O recorder mede monotonicamente receive, fim do parse, fim da atualização do livro, início/fim
da persistência e total local. A fila é limitada explicitamente a 100.000 eventos, com budget de
backlog de 5.000. Backlog não recuperado degrada; qualquer drop bloqueia. Não há `fsync` por
evento e o JSONL gzip permanece lossless e rotacionado.

`RecorderRuntimeHealth` registra queue depth/high watermark, recebidos, processados, pendentes,
drops, lag de processamento/persistência e stalls do loop. O manifest também preserva a
configuração e cada `LivenessIncident`.

## Estado atual, qualidade histórica e readiness

Current health (`READY`, `DEGRADED`, `NOT_READY`) descreve o fim da captura. Session quality
(`CLEAN`, `VALID_WITH_WARNINGS`, `INVALID`) preserva o histórico. Por isso
`DETECTED -> OBSERVING -> RECOVERED` pode terminar como current `READY` e session
`VALID_WITH_WARNINGS`; ele não vira falha permanente.

`READY_FOR_LONG_CAPTURE` requer os quatro streams, current health `READY`, zero incidente não
resolvido, gap real, drop e parser error, livro sincronizado, replay determinístico e fila
saudável. `READY_WITH_WARNINGS` aceita apenas incidentes recuperados sem impacto de integridade.
Qualquer falha desses budgets produz `NOT_READY_FOR_LONG_CAPTURE`.

O replay completo é executado logicamente duas vezes, sem sleep, comparando event count e hashes
de evento, estado final do livro, features observáveis e eventos de saúde. A consistência
cross-stream mede episódios até o livro refletir o best bid/ask observado, sem exigir igualdade
byte-a-byte entre conexões concorrentes.

## Fluxo de qualificação

```bash
adaptive-trader market microstructure record --market futures --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth,markPrice --depth-speed 100ms \
  --duration 300 --output-dir data/microstructure

adaptive-trader research microstructure futures-liveness-qualify \
  --session <qualification-300s-session> --previous-session <previous-300s-session> \
  --output-dir reports/research
```

Somente `READY_FOR_LONG_CAPTURE` ou `READY_WITH_WARNINGS`, com todos os warnings recuperados,
permite a captura de 1.800 s. O relatório final repete a qualificação com `--long-session` e cria
`long_capture_manifest.json`. `READY_FOR_4A3` ainda exige a captura longa concluída, ou uma
limitação externa explicitamente documentada com evidência representativa suficiente.

O diagnóstico antigo permanece `INCONCLUSIVE`: o gap local de 2.372,519 ms teve somente 102 ms
entre exchange event times e retomou com `pu` contínuo, sem snapshot/resync. Como a versão antiga
não media ping/pong, event-loop e estágios locais, não é possível separar honestamente jitter de
rede de buffering/processamento local.

## Resultado observado da Sprint 4A.2.2

O smoke formal de `299,022 s` registrou 37.711 eventos e terminou
`READY_FOR_LONG_CAPTURE/CLEAN`, sem incidentes, gaps, drops, resyncs, reconnects ou parser errors.
A fila chegou a 799 eventos e terminou vazia.

A captura longa de `1.798,663 s` registrou 310.393 eventos: 17.555 depth, 281.973 bookTicker,
9.063 aggTrade e 1.799 markPrice. O livro permaneceu sincronizado em 100% das observações, sem
gap real, resync, disconnect, reconnect, drop, parser error ou incidente de liveness. A fila
limitada chegou a 1.049/100.000, terminou vazia e o runtime ficou `READY`; houve três stalls do
loop, máximo 280,207 ms, sem perda. As quatro rotações comprimiram 437.398.663 bytes brutos para
54.470.072 bytes (`8,03x`).

Os dois replays de 310.393 eventos produziram hashes idênticos de evento, estado do livro,
features e health events. A consistência cross-stream teve 481 episódios de mismatch transitório,
mediana 71,461 ms, p95 411,365 ms, p99 653,127 ms, máximo 1.039,930 ms e zero mismatch não
resolvido. O gate final é `READY_FOR_4A3`.

A latência aparente de transporte da captura longa **não é uma medição one-way válida**:
112.146 amostras tiveram `receive_wall < exchange_event_time`, evidenciando desalinhamento/ajuste
do relógio cliente. Os valores brutos são preservados e marcados inválidos no relatório; não são
reinterpretados como rede ou event-loop. As métricas monotônicas locais permanecem utilizáveis:
processamento total mediano 7,328 ms, p95 49,419 ms e p99 90,151 ms; persistência mediana
0,062 ms, p95 0,407 ms e p99 1,700 ms.
