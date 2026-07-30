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

## Liquidação e manutenção

A manutenção usa taxa fixa configurada. Esse modelo é conservador e útil para comparação, mas
não reproduz tiers, deductions, insurance fund, ADL ou todas as regras da Binance.

A liquidação usa mark-price OHLC. Quando liquidação e stop/take-profit cabem no mesmo candle, a
ordem intrabar desconhecida é resolvida por `LIQUIDATION_FIRST`. A taxa de liquidação é debitada,
a posição é encerrada e saldo negativo é truncado em zero com estado `bankrupt`. O saldo nunca
volta ao capital inicial. Warnings documentam modelo aproximado, mark ausente e ambiguidade.

OHLC não revela caminho intrabar, gaps executáveis, profundidade, fila, impacto, latência, partial
fills ou variação de mark entre pontos. Logo, nenhuma equivalência com uma conta real é alegada.

## Pesquisa temporal

Desenvolvimento e validação podem comparar Spot baseline, Futures long, short espelhado,
long-short, time exits de 12/24 candles e alvo `R=2.5`, começando em `1x`. Só depois são exibidos
`2x` e `3x`, sem seleção automática.

O período consumido `2026-01-01T00:00:00Z` a `2026-07-01T00:00:00Z` é excluído da seleção de
mercado, modo, leverage, saída, funding policy e parâmetros. Ele não pode transformar retrospecto
em ajuste. Futuro holdout deve permanecer intocado até uma avaliação pré-registrada.

## Reprodutibilidade e interpretação

O manifest registra mercado, contrato, fonte, leverage, margem, manutenção, liquidação, funding,
hashes, custos, estratégia, modo, exclusão temporal e warnings. Resultados iguais dependem dos
mesmos dados e configuração. Métricas de retorno, drawdown, exposição, funding e liquidação são
descritivas; não provam lucratividade, significância, segurança ou desempenho futuro.
