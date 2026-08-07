# Simulação de execução intraday

## Escopo e fronteira de segurança

A Sprint 4A.2 adiciona uma venue local e determinística para responder perguntas mecânicas sobre
arrival, fill, fila, fee, slippage, posição e PnL executável. Ela recebe somente dados públicos ou
fixtures sintéticas. Não há cliente autenticado, credenciais, endpoint de ordens, Testnet, paper
trading externo ou caminho para enviar uma ordem. O modo é sempre `RESEARCH_ONLY`, com leverage
travada em `1x`; nenhuma política ou configuração é escolhida por PnL.

O alpha termina em `IntradayOrderIntent`. A camada de execução começa depois do governor:

```text
Alpha -> RiskGovernor -> IntradayOrderIntent -> ExecutionPlanner
      -> ExecutionSimulator -> SimulatedOrder/Fill -> PositionLedger -> PnL
```

O executor histórico de candles continua separado e compatível. O simulador intraday vive em
`adaptive_trader.execution` e não altera os sinais das pesquisas anteriores.

Para USD-M Futures, captura com feed health `NOT_READY` não pode alimentar diagnóstico de alpha
nem ser tratada como evidência de execução. `bookTicker`/`depth@100ms` chegam por conexão
`/public`; `aggTrade`/`markPrice@1s`, por conexão `/market`. A reconstrução usa a política `pu`
da exchange e o simulador recebe apenas replay já validado; detalhes estão em
`docs/FUTURES_MARKET_DATA_ROUTING.md`.

## Relógio e lifecycle

`SimulatedOrder` é imutável. Preços, quantidades, taxas, fees e PnL usam `Decimal`; todos os
timestamps são timezone-aware. O lifecycle possível é:

```text
CREATED -> IN_TRANSIT -> ACKNOWLEDGED
                         |-> WORKING -> PARTIALLY_FILLED -> FILLED
                         |              |-> CANCEL_PENDING -> CANCELED/EXPIRED
                         |-> FILLED
                         |-> REJECTED
```

Estados terminais são imutáveis. Fills duplicados, quantidade preenchida acima da solicitada,
remaining negativo ou estado terminal incoerente falham de forma explícita.

Os perfis determinísticos são configuração de pesquisa, não medições da infraestrutura do
usuário:

| Perfil | decisão | outbound | processamento | ack inbound | cancel outbound | cancel processamento | notificação fill |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `IDEALIZED` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `FAST` | 2 | 3 | 1 | 3 | 3 | 1 | 3 |
| `NORMAL` | 8 | 12 | 3 | 12 | 12 | 3 | 12 |
| `STRESSED` | 30 | 75 | 20 | 75 | 75 | 20 | 100 |

Uma decisão em `T` só chega à venue depois de `decision_latency + outbound_order_latency`. A
ordem usa o primeiro estado do livro pertencente ao mesmo mercado/símbolo cujo event time seja
igual ou posterior ao arrival. Livros anteriores nunca podem preencher essa ordem. Fixtures e
replay usam seed explícita; não há sleep estratégico.

## Market, marketable limit e partial fill

Uma market buy caminha somente pelas asks; market sell, somente pelos bids. A venue registra
quantidade solicitada/preenchida, níveis consumidos, melhor preço anterior, pior fill, VWAP,
depth consumido e remainder. Ela nunca inventa liquidez. O padrão é conservar o remainder como
partial fill; `REJECT_REMAINDER` solicita cancelamento atrasado.

`maximum_slippage_bps` restringe os níveis elegíveis em relação ao preço de referência. Uma
marketable buy limit nunca executa acima do limit; uma sell nunca executa abaixo. Se houver
remainder em `PARTIAL_FILL`, ele passa a trabalhar passivamente no limit. Uma passive limit não
preenche porque candle, mid ou melhor preço apenas tocou o nível.

## Maker, taker e aproximação de fila

`CONSERVATIVE_FIFO_APPROXIMATION` registra a quantidade pública visível à frente quando a ordem
entra. Trades agressivos contra o nível reduzem primeiro `queue_ahead_quantity` e só depois
podem preencher a quantidade própria. O simulador registra:

- fila estimada à frente;
- quantidade negociada através do nível;
- progresso estimado;
- confiança do fill;
- identificação explícita de que o modelo é uma aproximação.

Na política padrão `CONSERVATIVE`, redução de depth por cancelamento não melhora a posição. A
alternativa `PRO_RATA_DIAGNOSTIC` distribui a redução proporcionalmente e existe apenas como
diagnóstico. Nenhum relatório chama a posição de exata.

Cada `SimulatedFill` contém ID, order ID, event time, preço, quantidade, papel `MAKER`/`TAKER`,
fee, ativo da fee, livro anterior, latência de notificação e sequência. Uma ordem pode ter
múltiplos fills e o VWAP é calculado pelo notional de todos eles.

## Cancel, expiry e corrida com fill

Cancelamento percorre outbound e processamento antes de se tornar efetivo. Durante
`CANCEL_PENDING`, trades agressivos ainda podem gerar partial ou full fill. Um full fill vence a
corrida e não produz um segundo evento terminal; um partial preserva o cancel pendente até o
ack. Expiry apenas solicita esse mesmo cancel atrasado e mantém a vulnerabilidade até o instante
efetivo.

## Fees e decomposição de slippage

`FeeModel` aplica taxa em cada fill, separando Spot/Futures e maker/taker. Os defaults abaixo são
hipóteses documentadas e configuráveis para pesquisa, não uma afirmação sobre a conta do usuário:

| Mercado | maker | taker |
| --- | ---: | ---: |
| Spot | 0,10% | 0,10% |
| USD-M Futures | 0,02% | 0,05% |

Para taker, slippage vem do depth realmente consumido. `SlippageBreakdown` separa cruzamento do
spread, profundidade, latência e residual. O residual padrão é zero. A soma é
`total_execution_slippage_bps`; não se empilha um custo fixo opaco sobre o VWAP.

## Posição e PnL executável

`PositionLedger` mantém uma posição líquida por mercado/símbolo. Spot suporta long e caixa;
`OPEN_SHORT` é inválido. Futures suporta long, short ou flat sem hedge simultâneo. O ledger
registra quantidade, entrada média ponderada, PnL realizado, fees, funding, entrada e holding
time. Caixa Spot não pode ficar negativa sem uma política futura explícita.

Mark PnL e executable PnL são campos diferentes. O primeiro aceita mark price Futures para
margem, manutenção e liquidação. O segundo estima a realização caminhando bids para fechar long
ou asks para recomprar short. Se o depth não suporta toda a saída, o PnL executável fica
indisponível; mark price sozinho nunca solicita realização.

O `ELASTIC_300_150_V0` existente continua fixo: lucro líquido executável inclui fee de entrada,
fee estimada de saída, spread, depth e slippage; novo pico estende por 300 ms e reversão
microestrutural persistente confirma em 150 ms. Hard floor protegido sai imediatamente.
Deterioração de spread, depth, sincronização ou slippage aciona `LIQUIDITY_EXIT_FAILSAFE`.
Diagnósticos de reversão de preço, OFI, trade flow, depth e microprice permanecem separados; não
há busca de combinação ótima.

## Risco, liquidez e invariantes

Os presets `VERY_LOW`, `LOW` e `MODERATE` são pequenos, research-only e sempre `1x`. Eles não
representam recomendação nem ranking. `PortfolioRiskGovernor` pode ficar em `ACTIVE`, `REDUCED`,
`COOLDOWN`, `DAILY_KILLED` ou `DATA_KILLED`; loss streak, perda diária, slippage anormal, gaps,
desync e colapso de liquidez possuem reason codes.

O cap rejeita uma ordem quando `quantity / visible_executable_depth` excede
`maximum_visible_depth_fraction`. Eventos críticos — livro inválido, posição impossível,
quantidade negativa, mismatch contábil, sequência corrompida, limite diário ou falha de
invariante — acionam kill switch e bloqueiam novas aprovações.

Os checks persistidos incluem quantidade, remainder, fees, Spot short, duplicidade de fill e
leverage. O ledger também valida reconciliação de fills, caixa Spot e consistência de posição.

## Markouts e qualidade de execução

Markouts pós-fill usam 100/250/500 ms e 1/3/5/15/60 s. O CSV preserva maker/taker e efeito de
posição (`OPEN_LONG`, `CLOSE_LONG`, `OPEN_SHORT`, `CLOSE_SHORT`). Ausência de observação futura
fica vazia em vez de imputar preço. São diagnósticos `POST_EVENT_ONLY`, nunca input retroativo.

O relatório registra fill/partial/cancel/expiry/reject rate, maker/taker rate, latência, VWAP,
slippage, adverse selection disponível, quantidade não preenchida e tempo trabalhando. Mesma
sessão + configuração + seed gera os mesmos eventos, fills, posições, PnL e hashes.

## CLI e artefatos

```bash
adaptive-trader research execution synthetic \
  --scenario all --output-dir reports/research

adaptive-trader research execution simulate \
  --session data/microstructure/spot/ETHUSDT/<date>/<session> \
  --policy maker-first --latency-profile normal \
  --output-dir reports/research

adaptive-trader research execution show \
  --experiment reports/research/<execution-simulator-id>
```

Cada experimento contém exatamente 15 arquivos: manifest/config, resultados sintéticos,
lifecycle, fills, posições, qualidade, markouts, latência, consumo de liquidez, Elastic Exit,
governor, invariantes, determinismo e relatório Markdown. Não é criado candidate status
financeiro.

Captura pública prolongada aceita segundos explícitos ou execução até sinal:

```bash
adaptive-trader market microstructure record --market futures --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth,markPrice --depth-speed 100ms \
  --duration 300 --output-dir data/microstructure

adaptive-trader market microstructure health --session <futures-session>

adaptive-trader research microstructure futures-feed-harden \
  --session <futures-session> --previous-session <previous-30s-session> \
  --output-dir reports/research

adaptive-trader research microstructure futures-liveness-qualify \
  --session <futures-300s-session> --long-session <futures-1800s-session> \
  --output-dir reports/research

adaptive-trader market microstructure record --market spot --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth --depth-speed 100ms \
  --duration 0 --output-dir data/microstructure
```

`--duration 0` roda até SIGINT/SIGTERM. O handler solicita parada, fecha gzip, renomeia `.part`,
calcula hashes e escreve o manifest final. A captura continua estritamente pública.

A execução por 1.800 segundos só é liberada quando o relatório de 300 segundos declara
`READY_FOR_LONG_CAPTURE` ou `READY_WITH_WARNINGS` exclusivamente recuperado e sem perda de
integridade. Caso contrário, o resultado permanece `NOT_READY_FOR_LONG_CAPTURE`; aumentar duração
não corrige stream ausente, parser inválido, gap real ou livro desincronizado.

Liveness e execution latency são domínios diferentes. A instrumentação do recorder mede parse,
book update, fila e persistência; os perfis do simulador continuam hipóteses de arrival/order
latency e nunca são inferidos de `receive_wall - exchange_event_time`.

## Limitações

Dados públicos não revelam prioridade real da fila, ordens ocultas, matching interno, rejeições
privadas, congestionamento individual da conta ou fee tier. Latências são cenários, não medidas.
O book de 100 ms pode omitir eventos entre frames, e market impact além do depth visível não é
modelado. Markout vazio não é convertido em zero. Os 17 cenários A–Q e o replay Spot validam
mecânica e determinismo; não validam edge, frequência ou lucratividade.

## Preview taker para labels offline

Sprint 4A.3 adiciona ao mesmo simulador um preview não mutante para campaigns grandes. Ele valida
`OPEN_LONG`, `CLOSE_LONG`, `OPEN_SHORT` ou `CLOSE_SHORT` contra o lado da ordem, caminha os mesmos
níveis, mantém remainder/partial fill, calcula VWAP e usa o mesmo `FeeModel`. A otimização evita
alocar lifecycle e position ledger para centenas de milhares de hipóteses; não troca depth por
mid/mark e não inventa liquidez. O protocolo completo está em `docs/INTRADAY_EDGE_DISCOVERY.md`.
