# Adaptive Trader

Núcleo determinístico para pesquisa, coleta pública, backtest e paper trading de criptoativos em Binance Spot. Esta sprint não usa API key, autenticação, futuros, margem, alavancagem, IA, notícias, web ou operações reais.

## Pipeline

`BinancePublicClient` acessa somente klines públicos. `HistoricalCandleDownloader` pagina por período e faz upsert idempotente no SQLite schema v3. `MarketContextBuilder` valida candles fechados, ordem, símbolo, intervalo e horizonte temporal antes de calcular indicadores `Decimal`. A estratégia retorna somente `MarketSignal`; o `RiskManager` aprova ou rejeita e somente um `OrderIntent` aprovado chega ao executor local.

No backtest, a série completa fica no motor, mas a estratégia recebe apenas `candles[:T]`. A decisão no fechamento de `T` é executada na abertura futura `T + latency_candles`; execução no mesmo candle é rejeitada pela configuração. Stops e alvos usam os níveis antigos durante o OHLC corrente. Trailing e break-even só são calculados após o fechamento e valem para o candle seguinte. O executor aplica taxas, spread e slippage configurados em basis points; o caixa é validado pelo custo efetivo antes da compra. Stops e alvos têm política intrabar conservadora `STOP_FIRST`; posições abertas podem ser fechadas explicitamente no último candle.

O `BacktestEngine.run` aceita `evaluation_start_time`. Candles anteriores são input de warmup somente para indicadores: não criam snapshots, ordens, posições, equity curve ou métricas. `BacktestResult` separa `input_candle_count`, `warmup_candle_count` e `evaluated_candle_count`; `start_time` e `end_time` são sempre do período efetivamente avaliado. Sem esse argumento, o backtest simples preserva o comportamento anterior e considera todos os candles avaliados.

Dados permanentes são candles, decisões, ordens simuladas, fills, posições e snapshots. O `MarketContext` é recriado para cada análise e não mantém estado entre barras. Snapshots mantêm `day_start_equity`, `entries_today`, `orders_today` e `closed_trades_today`; o dia de negociação é UTC, com reset dos contadores na troca de data. O limite diário de entradas e a perda diária bloqueiam apenas novas compras; saídas protetivas continuam permitidas.

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

## Laboratório de research

A camada `adaptive_trader.research` organiza experimentos sem duplicar o `BacktestEngine`. Ela valida datasets imutáveis com SHA-256, cria holdout temporal e walk-forward rolling/expanding, aplica warmup sem permitir trades durante o warmup e registra hashes em manifests. Cada segmento informa início solicitado e início efetivo: quando o primeiro segmento não possui histórico anterior suficiente, os primeiros candles do próprio segmento maturam os indicadores e `WARMUP_REDUCED_EVALUATION_PERIOD` documenta a redução do período avaliado. Folds de validação sobrepostos, quando `step_days` é menor que a janela, permanecem contados individualmente.

Benchmarks `BUY_AND_HOLD` e `CASH`, cenários de custos, sensibilidade local, análise aproximada por regime e diagnósticos de concentração/overfitting são comparativos. Nenhum resultado é escolhido automaticamente para produção; o teste final não participa da seleção.

Exemplos offline, usando candles já persistidos:

```bash
adaptive-trader research dataset inspect --symbol ETHUSDT --interval 1m \
  --start 2024-01-01T00:00:00Z --end 2025-01-01T00:00:00Z
adaptive-trader research holdout run --symbol ETHUSDT --interval 1m \
  --start 2024-01-01T00:00:00Z --end 2025-01-01T00:00:00Z \
  --train-percent 60 --validation-percent 20 --test-percent 20 \
  --output-dir reports/research
adaptive-trader research walk-forward run --symbol ETHUSDT --interval 1m \
  --start 2024-01-01T00:00:00Z --end 2025-01-01T00:00:00Z \
  --train-days 90 --validation-days 30 --step-days 30 --mode rolling \
  --output-dir reports/research
adaptive-trader research report show --experiment reports/research/<experiment-id>
```

O método e suas limitações estão em `docs/RESEARCH_METHODOLOGY.md`; `research.example.toml` é apenas um exemplo e não contém credenciais.
