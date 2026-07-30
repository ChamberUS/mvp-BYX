# Adaptive Trader

Núcleo determinístico para coleta pública, pesquisa e backtest de `ETHUSDT` em Binance Spot e
Binance USD-M Futures. Futures existe somente como simulação histórica local com margem isolada
e alavancagem limitada a `3x`. Esta sprint não implementa autenticação, Testnet, paper trading,
ordens, IA, notícias, interface web ou operações reais.

## Pipeline

`BinancePublicClient` acessa somente klines públicos Spot. `BinanceFuturesPublicClient` acessa
somente os endpoints públicos USD-M de klines, mark-price klines e funding history. Os
downloaders paginam por período e fazem upsert idempotente no SQLite schema v4.
`MarketContextBuilder` valida candles fechados, ordem, símbolo, intervalo e horizonte temporal
antes de calcular indicadores `Decimal`. A estratégia Spot retorna somente `MarketSignal`; o
`RiskManager` aprova ou rejeita e somente um `OrderIntent` aprovado chega ao executor local.

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

Consultas de período ao SQLite tratam `--start` e `--end` como inclusivos para
`open_time`. Os limites efetivos de cada segmento e o hash do dataset ficam registrados nos
artefatos, evitando interpretações diferentes entre experimentos.

## Diagnósticos da Sprint 3A.2

Cada análise do `BacktestEngine` registra um `StrategyDecisionTrace` imutável com indicadores
disponíveis naquele instante, regime, direção, `reason_code`, decisão de risco e estado de
execução. O funil agrega candles elegíveis, filtros, sinais BUY, aprovações e ordens executadas.
Retornos futuros de sinais HOLD, MFE e MAE são calculados somente depois do backtest; esses
valores pós-evento nunca entram no `MarketContext` nem são vistos pela estratégia.

O período já consumido de `2026-01-01T00:00:00Z` a `2026-07-01T00:00:00Z` não pode participar
de seleção, ranking, OFAT ou escolha de timeframe. Os comandos de diagnóstico exigem a exclusão
explícita; a comparação de timeframe rejeita sobreposição. Intervalos ausentes são reportados e
nunca baixados automaticamente.

```bash
adaptive-trader research diagnose run \
  --symbol ETHUSDT --interval 1h \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z \
  --exclude-start 2026-01-01T00:00:00Z \
  --exclude-end 2026-07-01T00:00:00Z \
  --output-dir reports/research --yes

adaptive-trader research exits compare \
  --symbol ETHUSDT --interval 1h \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z \
  --output-dir reports/research/exits-compare --yes

adaptive-trader research costs walk-forward \
  --experiment reports/research/<walk-forward-experiment>

adaptive-trader research sensitivity ofat \
  --symbol ETHUSDT --interval 1h \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z \
  --exclude-start 2026-01-01T00:00:00Z \
  --exclude-end 2026-07-01T00:00:00Z \
  --output-dir reports/research --yes

adaptive-trader research timeframe compare \
  --symbol ETHUSDT --intervals 15m,1h,4h,1d \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z \
  --output-dir reports/research

adaptive-trader research diagnostics show \
  --experiment reports/research/<diagnostic-experiment>
```

O diagnóstico produz `decision_funnel.json/csv`, `hold_reason_analysis.csv`,
`entry_diagnostics.csv`, `exit_diagnostics.csv`, `entry_exit_decomposition.csv`,
`cost_scenarios_by_fold.csv`, `detailed_regime_metrics.csv`, `sensitivity_ofat.csv`,
`robustness_scorecard.json`, `candidate_assessment.json` e `diagnostics_report.md`. Arquivos não
aplicáveis ao comando mantêm cabeçalho válido e são explicados no relatório. OFAT altera apenas
um parâmetro permitido por vez; custos são avaliados por fold e consolidados. O assessment
`CANDIDATE`, `NOT_CANDIDATE` ou `INCONCLUSIVE` é somente uma classificação de pesquisa e nunca
habilita produção.

`diagnostics.example.toml` documenta períodos, horizontes e limiares sem credenciais. O método e
suas limitações estão em `docs/RESEARCH_METHODOLOGY.md`; `research.example.toml` também é apenas
um exemplo sem segredos.

## Validação controlada das hipóteses Spot

O catálogo imutável `spot-hypotheses-v1.toml` limita a Sprint 3A.4 a seis variantes: baseline,
time exits de 12/24 candles, alvo `R=2.5` e as duas combinações previamente registradas. A
execução usa somente `ETHUSDT 1h` local, compara saídas em `STRICT_TRENDING_UP` e depois compara
somente baseline e a vencedora com quatro modos de regime. `NO_REGIME_FILTER_DIAGNOSTIC` nunca é
candidata. Stop, target, time exit e fechamento final seguem essa ordem; ambiguidades entre stop
e target permanecem `STOP_FIRST`.

Selection usa exclusivamente development (`2022-01-01` a `2024-12-31`) e `BASE_COST`. A
configuração escolhida é bloqueada antes da confirmação em validation (`2025-01-01` a
`2025-12-31`). O período já consumido de 2026 não é carregado. Nenhuma busca ampla, Futures,
leverage, rede, API autenticada, paper trading ou ordem externa participa.

```bash
adaptive-trader research hypotheses spot run \
  --symbol ETHUSDT --interval 1h \
  --development-start 2022-01-01T00:00:00Z \
  --development-end 2024-12-31T23:00:00Z \
  --validation-start 2025-01-01T00:00:00Z \
  --validation-end 2025-12-31T23:00:00Z \
  --consumed-test-start 2026-01-01T00:00:00Z \
  --consumed-test-end 2026-07-01T00:00:00Z \
  --output-dir reports/research --yes

adaptive-trader research hypotheses spot show \
  --experiment reports/research/<experiment-id>
adaptive-trader research candidate freeze \
  --experiment reports/research/<experiment-id> --candidate-version 1
adaptive-trader research candidate inspect \
  --candidate configs/candidates/<candidate-id>.toml
adaptive-trader research candidate verify \
  --candidate configs/candidates/<candidate-id>.toml
```

Freeze falha se qualquer critério obrigatório não passar, se o modo for diagnóstico ou se a
versão já existir. Uma candidata congelada continua sendo apenas pesquisa e recebe a declaração
`NOT_APPROVED_FOR_PRODUCTION`. O future holdout é somente planejado; não é executado nesta sprint.

## Pesquisa USD-M Futures

O fluxo Futures é deliberadamente separado do `BacktestEngine` Spot:

- `FuturesBacktestEngine` mantém wallet, margem isolada, posição, PnL long/short, funding e
  liquidação próprios;
- `FuturesRiskManager` recebe `FuturesSignal` e só libera `FuturesOrderIntent` aprovado;
- sinais `ENTER_SHORT` não são confundidos com venda Spot;
- mark price é obrigatório para PnL não realizado, manutenção e liquidação;
- funding ausente falha por padrão; zero implícito é proibido;
- manutenção usa taxa fixa explícita e aproximada;
- em ambiguidade OHLC, liquidação tem prioridade `LIQUIDATION_FIRST`;
- leverage padrão é `1x`, margem é somente `ISOLATED` e valores acima de `3x` são rejeitados;
- `SPOT_PROXY_FOR_TESTS_ONLY` invalida o relatório e existe apenas para fixtures.

Spot e Futures possuem datasets, contabilidade, métricas e resultados separados. A comparação
gera `market_comparison.csv`, `market_comparison.json` e `market_comparison.md`; resultados nunca
são somados. O período consumido de `2026-01-01` a `2026-07-01` é removido de toda seleção.
Uma estratégia `NOT_CANDIDATE` em `1x` não pode virar candidata por amplificação de exposição.

Coleta pública explícita:

```bash
adaptive-trader market futures download-klines \
  --symbol ETHUSDT --interval 1h \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z
adaptive-trader market futures download-mark-price \
  --symbol ETHUSDT --interval 1h \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z
adaptive-trader market futures download-funding \
  --symbol ETHUSDT \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z
adaptive-trader market futures status --symbol ETHUSDT --interval 1h
```

`--start` e `--end` são inclusivos. Pesquisa nunca inicia esses downloads automaticamente.

Pesquisa local:

```bash
adaptive-trader research futures inspect \
  --symbol ETHUSDT --interval 1h \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z
adaptive-trader research futures backtest \
  --symbol ETHUSDT --interval 1h --mode long-short --leverage 1 \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z \
  --output-dir reports/research/futures
adaptive-trader research futures walk-forward \
  --symbol ETHUSDT --interval 1h --mode long-short --leverage 1 \
  --train-days 365 --validation-days 90 --step-days 90 \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z \
  --output-dir reports/research/futures-walk-forward
adaptive-trader research market compare \
  --symbol ETHUSDT --interval 1h --markets spot,futures \
  --futures-modes long,short,long-short --leverages 1,2,3 \
  --start 2022-01-01T00:00:00Z --end 2026-07-01T00:00:00Z \
  --exclude-start 2026-01-01T00:00:00Z \
  --exclude-end 2026-07-01T00:00:00Z \
  --output-dir reports/research/market-comparison --yes
```

Detalhes e limitações estão em `docs/FUTURES_RESEARCH_METHODOLOGY.md`.
