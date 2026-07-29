# Adaptive Trader

Núcleo determinístico para pesquisa, coleta pública, backtest e paper trading de criptoativos em Binance Spot. Esta sprint não usa API key, autenticação, futuros, margem, alavancagem, IA, notícias, web ou operações reais.

## Pipeline

`BinancePublicClient` acessa somente klines públicos. `HistoricalCandleDownloader` pagina por período e faz upsert idempotente no SQLite v2. `MarketContextBuilder` valida candles fechados, ordem, símbolo, intervalo e horizonte temporal antes de calcular indicadores `Decimal`. A estratégia retorna somente `MarketSignal`; o `RiskManager` aprova ou rejeita e somente um `OrderIntent` aprovado chega ao executor local.

No backtest, a série completa fica no motor, mas a estratégia recebe apenas `candles[:T]`. A decisão no fechamento de `T` é executada na abertura de `T+1`. O executor aplica taxas, spread e slippage configurados em basis points. Stops e alvos têm política intrabar conservadora `STOP_FIRST`; posições abertas podem ser fechadas explicitamente no último candle.

Dados permanentes são candles, decisões, ordens simuladas, fills, posições e snapshots. O `MarketContext` é recriado para cada análise e não mantém estado entre barras.

## Instalação

Requer Python `3.12+`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

As variáveis estão documentadas em `.env.example`. O carregamento lê apenas configurações; nenhuma credencial é solicitada ou armazenada.

## CLI

```bash
adaptive-trader doctor
adaptive-trader config show
adaptive-trader db init
adaptive-trader db status
adaptive-trader market download --symbol ETHUSDT --interval 1m \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z
adaptive-trader market update --symbol ETHUSDT --interval 1m
adaptive-trader market status --symbol ETHUSDT --interval 1m
adaptive-trader backtest run --symbol ETHUSDT --interval 1m \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z \
  --initial-balance 10000 --output reports/backtest-ethusdt.json
adaptive-trader backtest show --file reports/backtest-ethusdt.json
```

`market download` usa o transporte público e não pede credenciais. `market update` retoma após o último candle persistido. Relatórios JSON/CSV ficam em `reports/` e não são versionados.

## Qualidade

```bash
ruff check .
mypy src
pytest
pytest --cov=adaptive_trader --cov-report=term-missing
```

O CI executa esses checks em Python 3.12 para pushes e pull requests em `main`. As suposições detalhadas estão em `docs/BACKTEST_ASSUMPTIONS.md`.

## Segurança e limitações

`trading_enabled` permanece `false`. O motor pode usar um `DefaultRiskManager(local_simulation=True)` somente dentro do backtest local; isso não habilita trading externo. A estratégia não conhece banco, internet ou executor. Não existe caminho de envio de ordem para Binance.

O backtest não modela livro de ofertas, liquidez detalhada, fills parciais, impacto real, latência de rede ou ordem intrabar além da política conservadora documentada. Resultados passados não garantem resultados futuros.
