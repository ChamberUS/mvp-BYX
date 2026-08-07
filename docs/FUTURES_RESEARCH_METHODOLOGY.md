# USD-M Futures research methodology

## Escopo

Esta implementação existe apenas para coleta pública, pesquisa e backtest determinístico de
contratos perpétuos Binance USD-M. Não há autenticação, API key, Testnet, paper trading, envio de
ordens ou dinheiro real. Os endpoints públicos são chamados somente por comandos explícitos de
download; pesquisa lê exclusivamente o SQLite local.

Endpoints USD-M públicos utilizados:

- `GET /fapi/v1/klines`;
- `GET /fapi/v1/markPriceKlines`;
- `GET /fapi/v1/fundingRate`.

## Separação de mercado

Spot representa compra, posse e venda do ativo. Futures representa exposição contratual long ou
short, wallet, margem isolada, manutenção, funding e possibilidade de liquidação. Por isso
`BacktestEngine` e `FuturesBacktestEngine` não compartilham contabilidade, posição ou PnL.
`ENTER_SHORT` não é `SELL` Spot.

## Preços

- futures kline alimenta indicadores e o preço de referência de execução;
- spread e slippage movem o preço executado contra a posição;
- mark price calcula PnL não realizado, margem de manutenção e liquidação;
- index price é armazenável quando disponível, mas não substitui mark price;
- candles Spot não substituem candles Futures;
- `SPOT_PROXY_FOR_TESTS_ONLY` é restrito a fixtures, produz warnings fortes e invalida relatório.

Consultas e downloads tratam `start` e `end` como inclusivos. Candles, mark prices e funding são
deduplicados no schema v4 e recebem hashes independentes e combinado.

Para cada candle avaliado, mark price usa o mesmo `open_time` ou o último mark anterior com atraso
máximo de um intervalo. Nunca usa mark futuro, nearest bidirecional ou close Futures como proxy.
Readiness é `READY`, `READY_WITH_WARNINGS` ou `NOT_READY`; backtest real não inicia em
`NOT_READY`.

## PnL, margem e leverage

Para quantidade `q`:

```text
notional = execution_price * q
initial_margin = notional / leverage
long_pnl = (mark_price - entry_price) * q
short_pnl = (entry_price - mark_price) * q
maintenance_margin = mark_notional * maintenance_margin_rate
```

Leverage aumenta o notional possível para uma alocação de margem; não multiplica artificialmente
o retorno do preço. Uma posição só abre quando margem inicial, taxa de entrada e buffer cabem no
saldo. A sprint aceita apenas margem isolada, uma posição por vez, sem piramidagem, sem hedge
simultâneo e leverage de `1x` a `3x`.

Na Sprint 3A.5, apesar do limite estrutural legado, o orquestrador real rejeita qualquer leverage
diferente de `1x`; nenhuma execução 2x/3x é permitida.

Volatilidade maior não torna padrões mais fáceis: ela amplia ruído, custos, distância efetiva de
execução e risco de liquidação. Leverage também não cria edge. Uma estratégia negativa ou
`NOT_CANDIDATE` em `1x` não deve ser “salva” por exposição; o relatório registra
`LEVERAGE_AMPLIFIES_NON_CANDIDATE`.

## Funding

No timestamp de funding, apenas posições abertas recebem fluxo:

```text
funding_payment = mark_notional * funding_rate
```

Funding positivo: long paga e short recebe. Funding negativo: short paga e long recebe.
`funding_paid`, `funding_received`, `net_funding` e contagem de eventos ficam separados.
Ausência usa `FAIL` por padrão; zero nunca é presumido silenciosamente.

O histórico público pode fornecer `markPrice=""`; esse campo opcional é armazenado como ausente.
Nesse caso o cálculo usa `mark.open` já conhecido da hora, nunca `mark.close`. Funding no timestamp
de abertura precede uma nova entrada; evento posterior à entrada é aplicado à posição aberta.

## Liquidação e manutenção

A manutenção usa taxa fixa configurada. Esse modelo é conservador e útil para comparação, mas
não reproduz tiers, deductions, insurance fund, ADL ou todas as regras da Binance.

A ordem de eventos é funding, atualização do mark conhecido, liquidação, stop, take-profit,
time exit, saída por sinal e forced end. Quando liquidação e stop/take-profit cabem no mesmo
candle, a ordem intrabar desconhecida é resolvida por `LIQUIDATION_FIRST`; entre stop e alvo usa
`STOP_FIRST`. A taxa de liquidação é debitada,
a posição é encerrada e saldo negativo é truncado em zero com estado `bankrupt`. O saldo nunca
volta ao capital inicial. Warnings documentam modelo aproximado, mark ausente e ambiguidade.

OHLC não revela caminho intrabar, gaps executáveis, profundidade, fila, impacto, latência, partial
fills ou variação de mark entre pontos. Logo, nenhuma equivalência com uma conta real é alegada.

## Pesquisa temporal

Desenvolvimento e validação comparam somente as seis variantes pré-registradas: long, short
espelhado, long-short, time exits de 12/24 candles e alvo `R=2.5`, todas em `1x`. Parâmetros não
são alterados após validation e não há seleção adaptativa.

O período consumido `2026-01-01T00:00:00Z` a `2026-07-01T00:00:00Z` é excluído da seleção de
mercado, modo, leverage, saída, funding policy e parâmetros. Ele não pode transformar retrospecto
em ajuste. Futuro holdout deve permanecer intocado até uma avaliação pré-registrada.

## Reprodutibilidade e interpretação

O manifest registra mercado, contrato, fonte, leverage, margem, manutenção, liquidação, funding,
hashes, custos, estratégia, modo, exclusão temporal e warnings. Resultados iguais dependem dos
mesmos dados e configuração. Métricas de retorno, drawdown, exposição, funding e liquidação são
descritivas; não provam lucratividade, significância, segurança ou desempenho futuro.

## Diagnóstico de não-estacionariedade

A Sprint 3A.6 mantém exatamente as seis configurações 1x da Sprint 3A.5. Não altera EMAs, ATR,
volume, stop, alvo, time exit, custos, funding ou prioridade de eventos. O SQLite local é a única
fonte e o hash combinado esperado é obrigatório.

Uma execução cronológica BASE por configuração fornece trades e contextos point-in-time para
decomposições anuais, trimestrais, móveis, por fronteira, lado, regime e volatilidade. LOW, HIGH e
STRESS são executados separadamente com os mesmos cenários predefinidos. Funding-off também é
executado separadamente, mas somente como diagnóstico.

Trades são atribuídos temporalmente pelo timestamp de saída. Warmup não entra em métricas.
`HIGH_VOLATILITY` é uma etiqueta diagnóstica derivada do filtro ATR relativo já existente, não um
classificador treinado. Os quantis LOW/MEDIUM/HIGH/EXTREME são calculados somente em 2022-2024 e
congelados para aplicação em 2025.

Bootstrap opera sobre PnLs de trades fechados, com seed explícita e limite de 10.000 iterações.
Ele não embaralha candles, não altera sinais e não é evidência causal. Warnings de fronteira, ano
único, funding, custos e concentração são reportados separadamente.

As classificações finais descrevem estabilidade temporal e não declaram candidata. O comando
rejeita leverage diferente de `1x`, qualquer uso de 2026, hash divergente e ausência de readiness.
Não há download automático, rede, autenticação, ordem ou candidate freeze.

## Pullback continuation 1x

A Sprint 3B.1 adiciona `PullbackContinuationFuturesAnalyzer` sem alterar o analisador Futures
original. Ele implementa long, short espelhado e long-short com o catálogo fixo
`pullback-hypotheses-v1.toml`. A estratégia exige tendência estabelecida, persistência de três ou
seis candles, pullback controlado, retomada confirmada no fechamento, limite de extensão e os
filtros existentes de volume/ATR. `ENTER_SHORT` continua semanticamente separado de `SELL` Spot.

Nas variantes com perda de regime, a detecção no fechamento agenda uma saída futura. Funding e
mark são processados primeiro; liquidação, stop e alvo mantêm prioridade, seguidos por
regime-loss, time exit e forced end. Não há retroação intrabar.

Somente dados locais de 2022-2024 são carregados: 2022-2023 seleciona em BASE e 2024 valida o
lock. O intervalo consumido 2025-01-01 a 2026-07-01 é proibido para consulta, backtest e seleção.
As execuções usam exclusivamente `1x`; 2x/3x falham. LOW, BASE, HIGH e STRESS preservam o mesmo
funding real. Spot e Futures continuam com capital, posições, contabilidade, hashes e relatórios
separados. Consulte `PULLBACK_HYPOTHESIS_METHODOLOGY.md`.

## Trend following diário 1x

Na Sprint 3C.1, SMA 200 e canais Donchian são calculados em candles diários UTC derivados da fonte
local `1h`. O sinal confirmado no fechamento agenda execução para a abertura seguinte, mas funding
em timestamp real, mark `1h`, manutenção e liquidação continuam intradiários e têm prioridade.

Long, short e long-short são avaliados separadamente, sempre em margem isolada `1x`, sem hedge
simultâneo. Consulte
[`TREND_FOLLOWING_METHODOLOGY.md`](TREND_FOLLOWING_METHODOLOGY.md) para o catálogo, sizing,
risco defensivo, validation lock e limites temporais.
