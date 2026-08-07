# Pullback continuation hypothesis methodology

## Escopo e motivação

A Sprint 3B.1 testa uma nova hipótese determinística, pré-registrada e somente de pesquisa para
`ETHUSDT 1h`. Diagnósticos anteriores associaram melhor desempenho histórico à permanência em
`TRENDING_UP` ou `TRENDING_DOWN` e pior desempenho à transição para `RANGING`. Essa associação
motivou o teste, mas não demonstra causalidade nem garante lucro.

Não há IA, machine learning, otimização livre, Binance API, download automático, autenticação,
Testnet, paper trading, ordem externa, dinheiro real, leverage acima de `1x`, martingale,
averaging down, piramidagem ou candidate freeze.

## Catálogo pré-registrado

`pullback-hypotheses-v1.toml` contém exatamente:

1. `ORIGINAL_BASELINE`;
2. `PULLBACK_BASE`;
3. `PULLBACK_PERSISTENCE_6`;
4. `PULLBACK_TIME_EXIT_24`;
5. `PULLBACK_REGIME_LOSS_EXIT`;
6. `PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT`.

O arquivo e sua representação canônica recebem SHA-256. A execução falha se o catálogo mudar
enquanto o experimento estiver ativo. Não há variantes implícitas ou parâmetros escolhidos depois
dos resultados.

## Definição point-in-time

O `PullbackContinuationAnalyzer` recebe somente candles fechados até `T`. Cada decisão registra:

- tendência confirmada e persistência;
- detecção, validade, idade e profundidade do pullback em ATR;
- retomada;
- extensão;
- distância entre EMAs e do preço para cada EMA;
- ATR relativo e volume relativo;
- elegibilidade long/short;
- `reason_code`.

Não entram na estratégia máximo, mínimo, regime ou retorno futuro; não há janela centralizada,
confirmação retroativa ou nearest timestamp futuro. MFE, MAE e retornos depois de uma saída por
perda de regime são calculados apenas depois do backtest e nunca voltam ao analisador.

## Tendência, pullback e retomada long

Long exige:

1. regime `TRENDING_UP`;
2. EMA curta acima da EMA longa;
3. fechamento acima da EMA longa;
4. três ou seis candles de persistência, conforme a variante;
5. fechamento em direção à EMA curta sem cruzar a EMA longa;
6. profundidade entre `0.10` e `1.0` ATR;
7. duração entre um e seis candles;
8. fechamento novamente acima da EMA curta e acima do fechamento anterior;
9. distância até a EMA longa de no máximo `1.0` ATR;
10. os filtros preexistentes de volume e ATR relativo.

A confirmação ocorre no fechamento e a execução permanece na abertura futura.

## Short espelhado

Short Futures exige `TRENDING_DOWN`, EMA curta abaixo da longa, preço abaixo da EMA longa,
pullback para cima em direção à EMA curta sem cruzar a EMA longa e retomada com fechamento abaixo
da EMA curta e do fechamento anterior. Stop fica acima e alvo abaixo. `ENTER_SHORT` nunca é
tratado como `SELL` Spot. Long, short e long-short possuem métricas separadas e não são assumidos
simétricos.

## Persistência e overextension

Persistência conta somente candles consecutivos em que regime, EMAs e preço sustentam a base da
tendência. O estado do pullback guarda apenas informações observadas até o candle atual. Uma
extensão acima de `1.0` ATR da EMA longa rejeita a entrada; esse filtro é fixo, não treinado.

## Regime-loss exit

Nas duas variantes registradas, a saída é sinalizada quando uma posição long deixa
`TRENDING_UP` ou uma short deixa `TRENDING_DOWN`. A detecção usa o fechamento de `T` e só pode
executar na abertura futura. Não há saída retroativa intrabar.

Prioridade Spot:

1. stop-loss;
2. take-profit;
3. regime-loss exit;
4. time exit;
5. forced end.

Prioridade Futures:

1. funding aplicável;
2. mark update;
3. liquidação;
4. stop-loss;
5. take-profit;
6. regime-loss exit;
7. time exit;
8. forced end.

Entre stop e alvo prevalece `STOP_FIRST`; em Futures, liquidação prevalece
`LIQUIDATION_FIRST`.

## Política temporal

- development: `2022-01-01T00:00:00Z` a `2023-12-31T23:00:00Z`;
- validation bloqueada: `2024-01-01T00:00:00Z` a `2024-12-31T23:00:00Z`;
- referência consumida e proibida: `2025-01-01T00:00:00Z` a
  `2026-07-01T00:00:00Z`;
- future holdout: somente depois de `2026-07-01T00:00:00Z`.

O SQLite é consultado somente até o fim de 2024. Qualquer tentativa de usar 2025 ou 2026 em
development/validation falha. O intervalo consumido aparece apenas no manifest como excluído.

## Seleção e validation lock

Development usa folds rolling `365/90/90` e somente cenário `BASE`. O critério primário é a
mediana do retorno líquido dos folds. Desempates seguem, nessa ordem: percentual positivo,
menor pior drawdown, menos folds sem trades, menor concentração, mais trades e menor
complexidade registrada.

Uma variante só é elegível com mediana não negativa e pelo menos 50% dos folds positivos. Caso
nenhuma passe, o status é `NO_DEVELOPMENT_HYPOTHESIS`; validation executa apenas o baseline.
Caso haja elegíveis, no máximo duas são gravadas com baseline em um lock que inclui mercado,
modo, IDs, hash do catálogo e fingerprint de development. Validation não pode alterar o lock.

## Custos, funding e bootstrap

LOW, BASE, HIGH e STRESS são execuções reais e separadas do motor local. Somente BASE seleciona.
Funding público persistido não muda entre cenários. São emitidos os warnings
`LOW_COST_ONLY_EDGE`, `STRESS_COLLAPSE`, `COST_DOMINATED` e
`FUNDING_DOMINATED_RESULT` quando aplicáveis.

Bootstrap usa apenas PnLs de trades fechados, seed `42`, 2.000 iterações e intervalo de 95%.
Candles nunca são reamostrados nem apresentados novamente à estratégia.

## Classificação e holdout futuro

As classificações possíveis são `PROMISING_FOR_FUTURE_HOLDOUT`, `NOT_PROMISING`,
`INCONCLUSIVE` e `NO_DEVELOPMENT_HYPOTHESIS`. Todos os critérios registrados no manifest devem
passar para a primeira classificação. Mesmo assim, nenhuma candidata é congelada.

Se houver configuração promissora, o relatório cria somente um plano posterior a
`2026-07-01`, com no mínimo 90 dias e 20 trades fechados, configuração imutável e reinício após
qualquer mudança. Sem configuração promissora, o status é `NO_HOLDOUT_PLAN`.

## Limitações

O resultado depende de OHLC, custos simulados, latency fixa, funding histórico, mark price,
classificação aproximada de regime e modelos aproximados de margem/manutenção/liquidação.
Não representa fila, profundidade, impacto, partial fills ou execução real. Resultados
históricos não garantem resultados futuros e não constituem recomendação financeira.

## Sprint 3B.2: auditoria e calibração de frequência

O resultado zero-trade da 3B.1 motivou uma auditoria sequencial, não otimização financeira. O
reason code antigo `RESUMPTION_NOT_CONFIRMED` combinava cruzamento da EMA curta, fechamento
contra o candle anterior e retorno imediato do classificador ao regime. Essa última exigência
duplicava a persistência confirmada e podia fazer o pullback invalidar seu próprio estado. A
correção trava o regime no início do pullback; alinhamento, lado da EMA longa e filtros continuam
point-in-time.

O catálogo `pullback-calibration-v1.toml` contém exatamente `CALIBRATION_BASE`,
`EXTENSION_1_5`, `EXTENSION_2_0`, `NO_MINIMUM_DEPTH`, `VOLUME_RELAXED`,
`VOLATILITY_RELAXED`, `PERSISTENCE_2` e `DIRECTIONAL_CLOSE_RELAXED`. Cada item após a base altera
uma dimensão. Não há produto cartesiano, remoção simultânea de filtros ou busca automática.

Primeiro contam-se sinais, trades e folds somente em 2022–2023. Viabilidade requer 12 sinais, 10
trades, 50% dos folds com trades e no máximo 50% zerados; mais de cinco vezes a baseline
direcional ou exposição acima de 50% é `TOO_PERMISSIVE`. O desempate não recebe retornos. Depois
da escolha de no máximo duas definições, o lock registra parâmetros, hashes, dataset, commit,
critérios e frequência antes de 2024.

A ablação remove exatamente uma regra por vez sem executar sinais. Retornos de 1/3/6/12/24
candles, MFE e MAE são posteriores e marcados `POST_EVENT_ONLY_NO_STRATEGY_ACCESS`; não alteram
o catálogo. 2025–2026 continuam proibidos e leverage permanece exatamente 1x.
