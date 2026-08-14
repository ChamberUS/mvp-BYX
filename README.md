# Adaptive Trader

Núcleo determinístico para coleta pública, pesquisa e backtest de `ETHUSDT` em Binance Spot e
Binance USD-M Futures. Futures existe somente como simulação histórica local com margem isolada.
A validação real desta sprint aceita exclusivamente `1x`. Esta sprint não implementa autenticação,
Testnet, paper trading,
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

## Fundação de microestrutura intraday

A Sprint 4A.1 adiciona captura pública Spot/USD-M Futures, livro local com validação de sequência,
storage `JSONL gzip` com SHA-256, replay por relógio virtual, liquidez top 5/10/20, features
point-in-time, `NO_TRADE` e alphas long/short realmente separados. Short existe somente em
Futures `1x`. A frequência de 5–20 trades em dias ativos é diagnóstico, nunca quota ou objetivo
de calibração.

A Sprint 4A.2.1 endurece o feed USD-M conforme as rotas oficiais observadas em `2026-08-07`:
`bookTicker`/`depth@100ms` usam uma conexão `/public`, enquanto
`aggTrade`/`markPrice@1s` usam outra conexão `/market`. A URL legada e streams privadas são
rejeitadas. Futures alinha snapshot por `U <= lastUpdateId <= u` e depois encadeia apenas por
`pu == u anterior`; a regra Spot não é reutilizada.

```bash
adaptive-trader market microstructure doctor
adaptive-trader market microstructure record --market spot --symbol ETHUSDT \
  --streams aggTrade,bookTicker,depth --depth-speed 100ms \
  --output-dir data/microstructure --duration-seconds 60
adaptive-trader market microstructure inspect --session <session>
adaptive-trader market microstructure health --session <session>
adaptive-trader research microstructure replay --session <session> --speed max \
  --output-dir reports/research
adaptive-trader research microstructure alpha-diagnose --session <session> \
  --models long,short --output-dir reports/research
adaptive-trader research microstructure futures-feed-harden --session <session> \
  --previous-session <previous-30s-session> --output-dir reports/research
adaptive-trader research microstructure futures-liveness-qualify \
  --session <qualification-300s-session> --previous-session <previous-300s-session> \
  --long-session <long-1800s-session> --output-dir reports/research
```

O hardening Futures exige os quatro streams com parse válido, book sincronizado, liveness,
scorecard e replay idêntico duas vezes. O smoke inicial dura 300 s; somente um relatório
`READY_FOR_LONG_CAPTURE` autoriza a tentativa de 1.800 s. `NOT_READY` bloqueia alpha-diagnose.

A Sprint 4A.2.2 separa current health de qualidade histórica: um silêncio recuperado pode deixar
o feed atual `READY` e a sessão `VALID_WITH_WARNINGS`. Update speed não é presumido heartbeat;
depth/bookTicker são change-driven, aggTrade é execution-driven e markPrice@1s é aproximadamente
periódico. A fila do recorder agora é limitada e registra backlog, drops, event-loop stall,
parse, book update e persistência. Critérios, budgets e diagnóstico do stale anterior estão em
[`docs/MICROSTRUCTURE_LIVENESS_AND_QUALITY.md`](docs/MICROSTRUCTURE_LIVENESS_AND_QUALITY.md).

O `ElasticProfitExitController` é somente uma hipótese sintética 300/150 não selecionada. Ele usa
VWAP executável nos bids para fechar long e nos asks para recomprar short; mark price não realiza
lucro. Hard floor e failsafe de liquidez têm prioridade. Não há autenticação, ordem, Testnet,
paper trading ou seleção por PnL. Metodologia e limitações completas estão em
[`docs/MICROSTRUCTURE_RESEARCH_METHODOLOGY.md`](docs/MICROSTRUCTURE_RESEARCH_METHODOLOGY.md).

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
- leverage da validação real é exatamente `1x` e margem é somente `ISOLATED`;
- `SPOT_PROXY_FOR_TESTS_ONLY` invalida o relatório e existe apenas para fixtures.

Spot e Futures possuem datasets, contabilidade, métricas e resultados separados. A comparação
real 1x gera `spot_futures_1x_comparison.csv`, `.json` e `.md`; resultados nunca são somados.
O período consumido de `2026-01-01` a `2026-07-01` é registrado somente como excluído e não é
baixado ou carregado. Nenhuma variante 2x/3x é executada nesta sprint.

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
Cada comando registra páginas, requests, retries, duração, timestamps, duplicatas, hash e warnings
em `data/futures_download_audit.json`.

O inspect aplica `GapPolicy=WARN`, exige funding real e alinha mark pelo mesmo `open_time` ou pelo
último mark anterior com atraso máximo de um intervalo. Mark futuro, fallback silencioso para
close Futures e nearest bidirecional são proibidos. O resultado é `READY`,
`READY_WITH_WARNINGS` ou `NOT_READY`.

Pesquisa local:

```bash
adaptive-trader research futures inspect \
  --symbol ETHUSDT --interval 1h \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z
adaptive-trader research futures validate-real \
  --symbol ETHUSDT --interval 1h \
  --development-start 2022-01-01T00:00:00Z \
  --development-end 2024-12-31T23:00:00Z \
  --validation-start 2025-01-01T00:00:00Z \
  --validation-end 2025-12-31T23:00:00Z \
  --consumed-test-start 2026-01-01T00:00:00Z \
  --consumed-test-end 2026-07-01T00:00:00Z \
  --leverage 1 --output-dir reports/research --yes
adaptive-trader research futures validation-show \
  --experiment reports/research/<futures-real-experiment-id>
```

`validate-real` nunca baixa dados, rejeita leverage diferente de 1, rejeita uso de 2026 e falha
quando readiness, mark ou funding são inválidos. As seis variantes são pré-definidas, os folds
rolling usam 365/90/90 e não existe freeze automático. Detalhes e limitações estão em
`docs/FUTURES_RESEARCH_METHODOLOGY.md`.

## Robustez temporal Futures 1x

A Sprint 3A.6 reutiliza exclusivamente o dataset local e as seis variantes fixadas na Sprint
3A.5. Ela decompõe trades por ano, trimestre, janelas móveis, desenhos walk-forward, fronteiras,
lado, regime, volatilidade, funding, custos e concentração. Métricas de trade são atribuídas pelo
timestamp de saída; candles anteriores usados como warmup não entram nas métricas.

Os quantis de ATR relativo são definidos somente em 2022-2024 e aplicados sem recalibração a
2025. Retornos de mercado, distância e slope da EMA e persistência direcional são diagnósticos
pós-backtest e não alteram sinais. O bootstrap usa somente trades já fechados, seed explícita e
no máximo 10.000 iterações; candles nunca são embaralhados nem reapresentados à estratégia.

```bash
adaptive-trader research futures temporal-robustness \
  --symbol ETHUSDT --interval 1h \
  --start 2022-01-01T00:00:00Z --end 2025-12-31T23:00:00Z \
  --dataset-hash b4c9674c45ef10c96b68a72d84790aedfe6b93f638f23c63d4612ec61b6c570a \
  --leverage 1 --bootstrap-iterations 2000 --bootstrap-seed 42 \
  --output-dir reports/research --yes

adaptive-trader research futures temporal-show \
  --experiment reports/research/<temporal-robustness-id>
```

Os comandos são offline, não baixam dados, rejeitam 2026 e leverage diferente de `1x`, não
selecionam parâmetros e não congelam candidata. As classificações de robustez são diagnósticos de
pesquisa; não declaram lucratividade nem habilitam paper trading ou produção.

## Hipótese de continuação após pullback

A Sprint 3B.1 cria `PullbackContinuationAnalyzer` como estratégia research-only separada. O
catálogo imutável `pullback-hypotheses-v1.toml` contém somente baseline, pullback base,
persistência 6, time exit 24, regime-loss exit e a combinação persistência 6 + regime-loss.
As regras são point-in-time: tendência estabelecida, pullback de `0.10` a `1.0 ATR` por um a seis
candles, retomada confirmada no fechamento e extensão máxima de `1.0 ATR`.

Development usa exclusivamente 2022-2023 e BASE para selecionar no máximo duas variantes por
Spot long, Futures long, Futures short e Futures long-short. O lock é criado antes de validation,
que usa somente 2024. Todo o intervalo 2025-01-01 a 2026-07-01 é referência consumida proibida:
não é consultado nem executado. Futures usa somente `1x`; Spot e Futures permanecem separados.

```bash
adaptive-trader research pullback run \
  --symbol ETHUSDT \
  --interval 1h \
  --development-start 2022-01-01T00:00:00Z \
  --development-end 2023-12-31T23:00:00Z \
  --validation-start 2024-01-01T00:00:00Z \
  --validation-end 2024-12-31T23:00:00Z \
  --consumed-start 2025-01-01T00:00:00Z \
  --consumed-end 2026-07-01T00:00:00Z \
  --markets spot,futures \
  --futures-modes long,short,long-short \
  --leverage 1 \
  --output-dir reports/research \
  --yes

adaptive-trader research pullback show \
  --experiment reports/research/<pullback-experiment-id>
```

O comando é totalmente offline, não baixa dados, não autentica, não envia ordens e não cria
candidata. Gera exatamente 18 artefatos com funil, reason codes, development, validation,
walk-forward, custos, funding, lados, entradas, saídas por perda de regime, concentração,
bootstrap, assessment e plano de holdout. Metodologia completa:
`docs/PULLBACK_HYPOTHESIS_METHODOLOGY.md`.

## Calibração de frequência de pullbacks

A Sprint 3B.2 audita o resultado sem trades antes de calcular retornos. O trace separa regime
estabelecido, alinhamento de EMAs, lado da EMA longa, persistência, início/idade/profundidade do
pullback, cruzamento de retomada, fechamento direcional, extensão, volume e volatilidade. A
revalidação do regime no candle de retomada foi removida como requisito redundante e
incompatível com o estado de tendência já travado no início do pullback.

`pullback-calibration-v1.toml` contém somente a base e sete mudanças unitárias pré-registradas.
Viabilidade usa sinais, trades e cobertura dos folds em 2022–2023; retorno não participa da
seleção. No máximo duas definições por mercado/modo entram no lock imutável e só então são
reportadas financeiramente e validadas em 2024. Ablação remove uma regra por vez. Pós-eventos
recebem `POST_EVENT_ONLY_NO_STRATEGY_ACCESS`. Busca ampla e combinações continuam proibidas.

```bash
adaptive-trader research pullback calibrate \
  --symbol ETHUSDT --interval 1h \
  --development-start 2022-01-01T00:00:00Z \
  --development-end 2023-12-31T23:00:00Z \
  --validation-start 2024-01-01T00:00:00Z \
  --validation-end 2024-12-31T23:00:00Z \
  --consumed-start 2025-01-01T00:00:00Z \
  --consumed-end 2026-07-01T00:00:00Z \
  --markets spot,futures --futures-modes long,short,long-short \
  --leverage 1 --output-dir reports/research --yes

adaptive-trader research pullback calibration-show \
  --experiment reports/research/<experiment-id>
```

## Trend following diário pré-registrado

A Sprint 3C.1 testa, de forma offline e separada das estratégias anteriores, SMA 200 diária,
entrada Donchian 20, saídas Donchian 10/20 e risco fixo ou defensivo. Development usa 2022–2023,
o lock precede validation 2024 e todo o intervalo 2025–2026 permanece proibido; Futures opera
somente em `1x`, com funding e mark `1h` preservados.

O contrato completo de agregação UTC, regras point-in-time, execução no dia seguinte, catálogo,
seleção, artefatos e limites de segurança está em
[`docs/TREND_FOLLOWING_METHODOLOGY.md`](docs/TREND_FOLLOWING_METHODOLOGY.md).

## Simulação realista de execução intraday

A Sprint 4A.2 adiciona uma venue local, determinística e research-only para ordens market,
marketable limit e passive limit. Ela modela arrival time, perfis explícitos de latência, consumo
multinível, partial fills, maker/taker, fees por fill, aproximação FIFO conservadora, cancel-fill
race, expiry, posições Spot/Futures, mark PnL separado de executable PnL e governor de risco.
Spot short e leverage diferente de `1x` são rejeitados; não existe autenticação ou envio externo.

```bash
adaptive-trader research execution synthetic \
  --scenario all --output-dir reports/research

adaptive-trader research execution simulate \
  --session <microstructure-session> --policy maker-first \
  --latency-profile normal --output-dir reports/research

adaptive-trader research execution show \
  --experiment reports/research/<execution-simulator-id>
```

Capturas públicas mais longas usam `--duration 3600`; `--duration 0` permanece ativo até
SIGINT/SIGTERM e finaliza gzip e manifest de forma limpa. O desenho completo, defaults de fee,
invariantes, 17 cenários e limitações estão em
[`docs/INTRADAY_EXECUTION_SIMULATION.md`](docs/INTRADAY_EXECUTION_SIMULATION.md).

## Dataset de edge intraday executável

A Sprint 4A.3 agrega sessões públicas válidas em campaigns resumíveis, amostra anchors point-in-time
a cada 250 ms e calcula labels forward LONG/SHORT independentes em oito horizontes e três tiers de
notional. Entrada/saída caminham depth no `ExecutionSimulator`, incluem fees/slippage e nunca usam
mark ou mid como fill garantido. Features, labels e holdout permanecem fisicamente separados.

```bash
adaptive-trader market microstructure campaign-status \
  --campaign ethusdt-futures-intraday-v1

adaptive-trader research microstructure discover-edge \
  --campaign ethusdt-futures-intraday-v1 --anchor-ms 250 \
  --notionals 100,500,1000 --latency-profile normal \
  --output-dir reports/research --yes
```

A captura qualificada de ~30 minutos gerou 7.192 anchors, mas permanece `ENGINEERING_ONLY`:
`LONG_MORE_DATA_REQUIRED`, `SHORT_MORE_DATA_REQUIRED` e `MORE_DATA_REQUIRED`. Nenhum Alpha V1 foi
criado. Metodologia, campaign recording, temporal lock, block bootstrap, no-trade e Elastic real
data estão em [`docs/INTRADAY_EDGE_DISCOVERY.md`](docs/INTRADAY_EDGE_DISCOVERY.md).

## Economia de execução multi-day

A Sprint 4A.3.1 marca o campaign anterior como `ENGINEERING_CONSUMED` e inicia
`ethusdt-futures-intraday-discovery-v1` sem reutilizar suas sessões. O catálogo imutável compara
taker/taker, maker/taker, taker/maker e maker/maker; labels novos cobrem até 15 minutos sem cruzar
capture boundaries. Os runners de 10m/15m são controladores independentes e não modificam o
baseline histórico Elastic 300/150.

A primeira sessão nova cobre apenas 59,012 s. Todos os labels longos são `LABEL_INCOMPLETE`, e
políticas/runners permanecem `MORE_DATA_REQUIRED`. O comando de continuação até 24h/duas datas,
maker queue, episódios não sobrepostos e limitações estão em
[`docs/MULTI_DAY_EXECUTION_ECONOMICS.md`](docs/MULTI_DAY_EXECUTION_ECONOMICS.md).

## Qualificação econômica multi-day

A Sprint 4A.3.2 corrige a proveniência de captura: novas sessões gravam SHA, estado clean/dirty,
branch, versão e hash da configuração. O raw anterior não foi reescrito e é rejeitado da amostra
científica por proveniência incompleta. Uma nova sessão clean de 59,016 s foi admitida, mas o
campaign ainda tem somente uma data e permanece `ENGINEERING_ONLY`.

A resposta científica central é **MORE_DATA_REQUIRED**; nenhum lado, policy, notional ou runner foi
declarado vencedor, e o holdout permanece fechado. Critérios, operação resumível e limitações estão
em [`docs/MULTI_DAY_ECONOMIC_QUALIFICATION.md`](docs/MULTI_DAY_ECONOMIC_QUALIFICATION.md).

## Aquisição 24h — Sprint 4A.3.3

O campaign científico avançou para oito sessões admitidas, 5.624,936 segundos válidos, duas
datas UTC e 823.399 eventos científicos. A condição de datas foi atingida, mas ainda faltam
80.775,064 segundos para 24h; portanto o estado é `DATA_COLLECTION_IN_PROGRESS`,
discovery/confirmation continuam fechados, o holdout permanece `LOCKED` e a resposta é
`MORE_DATA_REQUIRED`. Nenhum resultado financeiro foi inspecionado para seleção prematura.

O checkpoint mais recente adicionou um chunk completo de 1.798,255 s e um segundo chunk encerrado
com `SIGINT` de forma segura, com 44,430 s. Ambos foram admitidos com provenance completa, zero
gaps, drops e erros de parser, book sincronizado e replay determinístico. O raw ocupa 222,863 MiB,
permanece ignorado pelo Git e há 148,836 GiB livres. Ainda faltam 15.975,064 s para o checkpoint
operacional de 6 h.

O checkpoint, hashes congelados e comando resumível estão em
[`docs/SPRINT_4A_3_3_24H_DATA_QUALIFICATION.md`](docs/SPRINT_4A_3_3_24H_DATA_QUALIFICATION.md).
