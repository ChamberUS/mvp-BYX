# Metodologia da hipótese de trend following diário

## Escopo e status da pré-inscrição

A Sprint 3C.1 abre uma linha experimental nova, determinística e somente de pesquisa para
`ETHUSDT`. A hipótese combina filtro macro por SMA de 200 dias, entrada por rompimento Donchian
de 20 dias, saída Donchian de 10 ou 20 dias e dimensionamento por risco. Spot é long-only.
Futures USD-M testa long, short e long-short separadamente, sempre com margem isolada e
alavancagem exatamente `1x`.

As linhas anteriores permanecem encerradas: o baseline direcional não é candidato e a família de
pullback foi classificada como restritiva e com amostra insuficiente. Esta sprint não modifica,
relaxa nem tenta recuperar essas estratégias.

Esta é uma pré-inscrição, não uma afirmação de lucratividade. Não há IA, machine learning,
otimização livre, busca de períodos, novos indicadores, múltiplos ativos, múltiplos timeframes,
paper trading, Testnet, API autenticada, download automático, ordem externa, dinheiro real ou
congelamento de candidata.

Também ficam fora do catálogo: otimização genética ou bayesiana, grid amplo, outras médias ou
períodos de entrada/saída, RSI, MACD, filtros de volume ou ATR para entrada, take-profit fixo,
trailing ATR, pullback, martingale, averaging down, piramidagem, grid trading, margem cross e
leverage `2x`/`3x`. Nenhum desses elementos pode ser introduzido depois de observar development
ou validation.

## Política temporal e warmup

Os intervalos UTC são fixos:

- development: `2022-01-01T00:00:00Z` a `2023-12-31T23:00:00Z`;
- validation bloqueada: `2024-01-01T00:00:00Z` a `2024-12-31T23:00:00Z`;
- referência consumida e proibida: `2025-01-01T00:00:00Z` a
  `2026-07-01T00:00:00Z`.

Development pode selecionar no máximo uma configuração para cada grupo de mercado/modo.
Validation somente confirma ou rejeita o que já estiver no lock; ela não seleciona, reordena,
substitui ou altera parâmetros. O intervalo consumido aparece apenas como excluído no manifest:
2025 e 2026 não podem ser consultados, carregados, agregados nem executados. Sobreposição entre
intervalos ou divergência dos limites pré-registrados causa falha.

A SMA requer 200 fechamentos diários. Os primeiros 199 candles diários disponíveis em cada série
são exclusivamente warmup: não geram sinais, trades, snapshots financeiros, exposição ou
métricas. Não se fabrica histórico anterior. O experimento registra o início solicitado, a
quantidade de warmup e o primeiro dia efetivamente avaliável. Quando a falta de histórico reduz a
janela, emite `WARMUP_REDUCED_EVALUATION_PERIOD`.

Development não lê candles anteriores a `2022-01-01`; portanto, seus primeiros 199 dias válidos
são warmup. Em um fold, candles cronologicamente anteriores dentro de 2022–2023 podem aquecer os
indicadores, mas somente o segmento de avaliação produz sinais e métricas. Validation e cada
trimestre de 2024 podem usar os 199 fechamentos imediatamente anteriores já pertencentes ao
dataset 2022–2024 bloqueado, inclusive 2023, apenas como contexto técnico. Esse reaproveitamento
não recalibra parâmetros nem atribui métricas ao warmup. Nenhum warmup pode vir de antes de 2022,
depois do instante avaliado ou do intervalo consumido.

## Agregação diária canônica

`DailyCandleAggregator` transforma somente candles `1h` locais e persistidos em candles `1d`.
Todos os limites usam UTC; timezone local nunca define o dia. Para cada dia UTC:

- `open` é a abertura da primeira hora;
- `high` é a maior máxima horária;
- `low` é a menor mínima horária;
- `close` é o fechamento da última hora;
- `volume` e `quote_volume` são somados;
- `trade_count` é somado;
- `open_time` é `00:00:00Z`;
- `close_time` é o fechamento da última hora;
- `is_closed` só é verdadeiro quando o dia está completo.

Duplicatas, gaps, ordem inválida e horas ausentes são registrados como falhas de integridade. Uma
hora ausente nunca é preenchida ou inferida. A política de dia incompleto possui exatamente três
modos:

- `FAIL`: interrompe a execução;
- `WARN_AND_EXCLUDE`: registra e exclui o dia, sendo o padrão da pesquisa válida;
- `ALLOW_DOCUMENTED`: aceita somente por escolha explícita e documenta a limitação.

O experimento gera hashes SHA-256 separados para candles diários Spot, candles diários Futures e
configuração da agregação. A identidade diária inclui o conteúdo dos candles `1h` de origem; mudar
qualquer candle horário utilizado precisa mudar o hash diário. Spot e Futures possuem domínios de
hash distintos e nunca colidem ou compartilham dataset.

## Indicadores point-in-time

Todos os indicadores usam apenas dados conhecidos até o fechamento diário `t`. Não há centered
window, nearest timestamp futuro ou revisão retroativa.

Para o dia `t`:

- `sma_200[t]` é a média dos 200 fechamentos diários terminando em `t`;
- `donchian_entry_high[t]` é a maior máxima dos 20 dias anteriores;
- `donchian_entry_low[t]` é a menor mínima dos 20 dias anteriores;
- a saída long de 10 ou 20 dias usa a menor mínima dos respectivos dias anteriores;
- a saída short de 10 ou 20 dias usa a maior máxima dos respectivos dias anteriores.

O candle atual participa da SMA, mas é excluído de todos os canais Donchian. Portanto, uma máxima
ou mínima intradiária do próprio dia não desloca o nível contra o qual o fechamento desse dia é
testado.

## Entradas long e short

Uma entrada long exige candle diário fechado, SMA disponível, `close[t] > sma_200[t]`,
`close[t] > previous_20_day_high[t]`, ausência de posição e aprovação de risco e capital. Uma
máxima intradiária acima do canal sem fechamento acima dele não é breakout. O funil usa, entre
outros, `WARMUP_INCOMPLETE`, `MACRO_FILTER_LONG_REJECTED`,
`DONCHIAN_LONG_BREAKOUT_NOT_CONFIRMED`, `POSITION_ALREADY_OPEN`, `RISK_REJECTED` e
`ENTER_LONG_APPROVED`.

Short existe somente em Futures e exige candle fechado, SMA disponível,
`close[t] < sma_200[t]`, `close[t] < previous_20_day_low[t]`, modo que permita short, ausência de
posição e aprovação de risco e margem. Os reason codes específicos incluem
`MACRO_FILTER_SHORT_REJECTED`, `DONCHIAN_SHORT_BREAKOUT_NOT_CONFIRMED`, `SHORT_NOT_ALLOWED` e
`ENTER_SHORT_APPROVED`. `ENTER_SHORT` não reutiliza a semântica de `SELL` Spot.

O modo long-short mantém no máximo uma posição por vez. Não há hedge simultâneo, piramidagem,
averaging down ou nova entrada enquanto uma posição estiver aberta.

## Saídas e timing de execução

Long sinaliza saída quando o fechamento diário fica abaixo da SMA 200 ou abaixo do canal de saída
Donchian de 10 ou 20 dias definido pela variante. Short usa as condições espelhadas: fechamento
acima da SMA 200 ou acima do canal de saída. O rompimento de saída também precisa ser confirmado
no fechamento; o canal estrutural não é um stop intradiário nesta versão.

Uma decisão confirmada no fechamento de `t` somente pode executar na abertura futura, usando o
primeiro candle `1h` elegível do dia UTC seguinte. Entrada e saída aplicam spread, slippage e
taxas. Não há execução no mesmo candle diário, retroação para a máxima ou mínima do dia nem uso da
abertura seguinte antes de ela existir.

Prioridade Spot:

1. saída macro pela SMA;
2. saída Donchian;
3. `FORCED_END`.

Prioridade Futures:

1. funding aplicável;
2. atualização de mark price;
3. liquidação;
4. saída macro pela SMA;
5. saída Donchian;
6. `FORCED_END`.

Se macro e Donchian forem verdadeiros no mesmo fechamento, as duas condições são registradas,
`MACRO_FILTER_EXIT` é o reason principal e há uma única saída pelo mesmo preço futuro. O fechamento
forçado é a única exceção à abertura do dia seguinte: no limite final, fecha pela última cotação
executável conhecida dentro da própria avaliação, com custos, e é identificado separadamente.
Spot usa o fechamento do último candle fonte elegível; Futures processa funding, mark e eventual
liquidação até o limite e usa o fechamento do último futures kline elegível como referência de
execução. `FORCED_END` nunca atravessa development para validation nem validation para 2025.

## Relógio intradiário de Futures

O sinal de estratégia é diário, mas a contabilidade e a proteção Futures continuam horárias.
Funding histórico é aplicado em seu timestamp real. Mark price `1h` atualiza PnL não realizado,
manutenção e teste de liquidação; o fechamento diário nunca substitui o mark. Se funding, mark,
liquidação e uma saída diária pendente coincidirem, o motor processa funding e mark, testa
liquidação e só depois considera a saída agendada.

Liquidação possui prioridade `LIQUIDATION_FIRST` sobre qualquer saída diária. A detecção de uma
saída no fechamento não protege retroativamente a posição durante esse dia. A aproximação por
OHLC, a taxa fixa de manutenção e a ausência de tiers, ADL e insurance fund permanecem limitações
explícitas.

## Dimensionamento por risco

No modo `NORMAL`, o orçamento de risco é `current_equity * 0.01`. No modo `DEFENSIVE`, é
`current_equity * 0.005`. Para long, o stop estrutural inicial é o Donchian exit low vigente na
entrada; para short, é o Donchian exit high. A quantidade bruta é:

```text
risk_per_unit = abs(estimated_entry_price - initial_structural_stop)
quantity = risk_budget / risk_per_unit
```

O stop estrutural inicial é referência de sizing. Ele não cria stop Donchian intradiário e não
altera a regra de saída confirmada no fechamento.

O canal estrutural observado no fechamento do sinal fica congelado na ação pendente. Quando a
primeira abertura `1h` elegível do dia seguinte existe, o sizing é recalculado uma única vez com
o equity e o saldo disponíveis naquele instante. `estimated_entry_price` é essa abertura ajustada
adversamente por spread e slippage; a taxa é debitada separadamente e participa do teste de
capacidade. Um gap que coloque o stop no lado incorreto, zere a distância ou torne a ordem
inviável rejeita a entrada; não se usa o fechamento do sinal, não se desloca o stop e não se tenta
outra hora.

Depois do cálculo, a quantidade é limitada por caixa Spot, wallet Futures, margem inicial,
maximum position percent, notional, taxas, spread, slippage, precisão e quantidade mínima. A
entrada é rejeitada se a distância for zero ou negativa, se o stop estiver no lado incorreto, se
a quantidade quantizada for zero, se o custo não couber ou se o notional exceder o limite. Os
reason codes incluem `INVALID_INITIAL_STOP`, `ZERO_RISK_DISTANCE`, `POSITION_SIZE_ZERO`,
`CASH_INSUFFICIENT`, `MARGIN_INSUFFICIENT`, `NOTIONAL_LIMIT` e `POSITION_SIZE_APPROVED`.

## Estado de risco defensivo

`RiskMode` começa em `NORMAL`, com risco de 1%. Conta como perda estrutural somente trade fechado
com PnL líquido negativo por `DONCHIAN_EXIT_10`, `DONCHIAN_EXIT_20` ou `MACRO_FILTER_EXIT`.
`FORCED_END`, saída administrativa, posição aberta e liquidação não incrementam a sequência. Um
trade positivo antes da terceira perda interrompe e zera a sequência.

Na terceira perda estrutural consecutiva:

- o modo muda para `DEFENSIVE`;
- o risco cai para 0,5%;
- `equity_recovery_target` recebe o equity imediatamente anterior ao primeiro trade da sequência.

O modo defensivo permanece até `current_equity >= equity_recovery_target`. Uma vitória que não
alcance o target não restaura 1%; uma nova perda não cria target novo nem reduz novamente o risco.
Ao recuperar, o modo volta a `NORMAL`, a sequência é zerada e o target é limpo.

Cada trade persiste modo, percentual de risco, perdas consecutivas, target, equity antes/depois e
timestamps de ativação e recuperação. Uma liquidação inesperada em Futures `1x` ativa
imediatamente modo defensivo e kill state do dia, além de emitir
`UNEXPECTED_LIQUIDATION_AT_1X`.

Essa liquidação é um override de segurança inclusive nas variantes `FIXED`: qualquer nova entrada
do experimento usa 0,5% até recuperar o target. O run deixa de representar risco fixo puro,
registra a exceção e não pode receber `PROMISING_FOR_CONFIRMATION`, pois essa classificação exige
zero liquidações em `1x`.

## Catálogo pré-registrado

`trend-following-hypotheses-v1.toml` contém exatamente seis variantes, nesta ordem:

1. `TF_DONCHIAN_20_FIXED_RISK`: saída 20, risco fixo de 1%;
2. `TF_DONCHIAN_10_FIXED_RISK`: saída 10, risco fixo de 1%;
3. `TF_DONCHIAN_20_DEFENSIVE_RISK`: saída 20, risco 1%/0,5%;
4. `TF_DONCHIAN_10_DEFENSIVE_RISK`: saída 10, risco 1%/0,5%;
5. `TF_LONG_ONLY_DONCHIAN_20`: long-only, saída 20, risco fixo de 1%;
6. `TF_SHORT_ONLY_DONCHIAN_20`: Futures short-only, saída 20, risco fixo de 1%.

Todas usam SMA 200 e entrada Donchian 20. Não existem outros períodos, médias, regras de
recuperação ou combinações implícitas. A representação canônica, o arquivo literal, a ordem e a
complexidade recebem identidade própria; o experimento falha se o catálogo mudar.

## Grupos de mercado e aplicabilidade

Os quatro grupos são independentes:

| Grupo | Variantes aplicáveis |
| --- | --- |
| `SPOT/LONG` | A, B, C, D e E |
| `FUTURES/LONG` | A, B, C, D e E |
| `FUTURES/SHORT` | A, B, C, D e F |
| `FUTURES/LONG_SHORT` | A, B, C e D |

Spot e Futures não compartilham capital, posições, contabilidade, hashes ou resultados. Resultados
dos grupos nunca são somados. Os benchmarks também não participam da seleção.

## Development, walk-forward e viabilidade

Todas as variantes aplicáveis são executadas em development 2022–2023. Além de sinais, trades,
retornos bruto/líquido, win rate, profit factor, expectancy e mediana de trade, o relatório inclui
trades por ano e lado, drawdown, return-to-drawdown, exposição, custos, funding, liquidações,
ativações, candles e trades em modo defensivo, duração da redução de risco, resultado sem o melhor
trade, concentração dos três melhores, folds sem trades, mediana walk-forward e percentual de
folds positivos.

O walk-forward de development é rolling com `train_days = 365`, `validation_days = 90` e
`step_days = 90`. Warmup anterior é permitido, mas não integra a avaliação. Quando não houver 200
dias de warmup, a avaliação é reduzida e recebe warning; histórico não é fabricado. Validation
2024 usa janelas trimestrais fixas e não ajusta parâmetros.

Antes de comparar resultado, a suficiência operacional exige:

- pelo menos 8 trades em development;
- pelo menos 50% dos folds com trades;
- no máximo 50% dos folds sem trades;
- no máximo 90% de exposição.

Os status são `OPERATIONALLY_VIABLE`, `TOO_RESTRICTIVE`, `TOO_PERMISSIVE` e
`INSUFFICIENT_SAMPLE`. Os limites não podem ser relaxados após observar os resultados.

## Seleção de development

Somente variantes `OPERATIONALLY_VIABLE`, executadas em development com custo `BASE`, podem ser
selecionadas. Há no máximo uma seleção para cada grupo. O critério primário é
`median_walk_forward_net_return`; os desempates, na ordem fixa, são:

1. maior percentual de folds positivos;
2. menor pior drawdown;
3. menor concentração dos três melhores trades;
4. mais trades;
5. menor exposição;
6. menor complexidade;
7. ordem do catálogo.

Se nenhuma variante tiver mediana não negativa e pelo menos 50% de folds positivos, o grupo recebe
`NO_DEVELOPMENT_HYPOTHESIS`. A configuração menos ruim não é escolhida silenciosamente.

## Validation lock e validation 2024

Antes de qualquer execução de 2024, `trend_following_validation_lock.json` registra:

- configuração e parâmetros selecionados por grupo;
- catálogo, hash canônico e hash do arquivo;
- hashes dos datasets e da agregação;
- períodos e métricas de development;
- critérios e ordem de seleção;
- commit e estado `git dirty`;
- leverage, cenários e parâmetros de custo;
- modelo de risco;
- timestamp de seleção e fingerprint do lock.

Depois do lock, bytes, configurações e fingerprints precisam permanecer idênticos. Não se pode
adicionar variante, substituir vencedora, voltar ao development ou selecionar com métricas de
validation. Em 2024 executam-se somente benchmarks e configurações bloqueadas. O relatório de
validation registra trades, retorno, walk-forward trimestral, folds positivos, drawdown, custos,
funding, efeito do risco defensivo, concentração e bootstrap.

## Risco fixo versus defensivo

Os pares equivalentes são saída 20 fixa versus defensiva e saída 10 fixa versus defensiva, sempre
dentro do mesmo mercado, modo e período. `defensive_risk_comparison.csv` registra diferenças de
retorno, drawdown, volatilidade, maior perda e tempo de recuperação, além de ativações, percentual
em modo defensivo, trades a 0,5%, upside sacrificado e downside evitado.

As classificações são `DRAWDOWN_IMPROVED`, `RETURN_REDUCED`, `RECOVERY_DELAYED`,
`NO_MATERIAL_EFFECT` e `INSUFFICIENT_SAMPLE`. Reduzir risco não é presumido benéfico: melhora de
drawdown e atraso ou perda de recuperação são reportados independentemente.

## Custos, funding, benchmarks e contribuição por lado

`LOW`, `BASE`, `HIGH` e `STRESS` são execuções separadas; somente `BASE` seleciona. O mesmo funding
histórico real é preservado em todos os cenários. Os diagnósticos podem emitir
`LOW_COST_ONLY_EDGE`, `STRESS_COLLAPSE`, `COST_DOMINATED` e
`FUNDING_DOMINATED_RESULT`.

Benchmarks Spot são `CASH` e `SPOT_BUY_AND_HOLD`. Benchmarks Futures são `CASH`,
`FUTURES_LONG_1X` e `FUTURES_SHORT_1X`, com custos, funding e liquidação quando aplicáveis.
Benchmarks não são selecionáveis. Long, short e long-short recebem contribuição, exposição e
concentração separadas; uma contribuição não é assumida estável pela média agregada.

## Bootstrap e concentração

Bootstrap opera somente sobre PnLs líquidos de trades fechados, nunca sobre candles. Usa seed
`42`, 2.000 iterações e intervalo percentil de 95% para mean trade PnL, median trade PnL, total
PnL, expectancy e win rate. Os status são `POSITIVE_UNCERTAIN`, `NEGATIVE_UNCERTAIN`,
`INCLUDES_ZERO` e `INSUFFICIENT_SAMPLE`.

Concentração inclui resultado sem o melhor trade, participação dos três melhores trades e
resultado sem esses três. Bootstrap e concentração são diagnósticos de incerteza e dependência de
outliers; não reordenam sinais nem retornam informação à estratégia.

## Classificação final

Cada configuração recebe um dos status `PROMISING_FOR_CONFIRMATION`, `NOT_PROMISING`,
`OPERATIONALLY_VIABLE_BUT_UNPROVEN`, `TOO_RESTRICTIVE`, `INSUFFICIENT_SAMPLE` ou
`NO_DEVELOPMENT_HYPOTHESIS`.

`PROMISING_FOR_CONFIRMATION` exige cumulativamente:

- viabilidade operacional e pelo menos 8 trades em development;
- pelo menos 4 trades em validation;
- mediana walk-forward não negativa em development e validation;
- pelo menos 50% de folds positivos em ambos;
- retorno líquido de validation não negativo;
- drawdown máximo de no máximo 15%;
- no máximo 50% de folds sem trades;
- cenário STRESS não completamente destrutivo;
- concentração do melhor trade de no máximo 50%;
- resultado sem os três melhores trades não fortemente negativo;
- bootstrap não fortemente negativo;
- nenhuma liquidação Futures em `1x`;
- nenhuma leitura de período consumido;
- validation lock intacto.

Para esses critérios, drawdown máximo é o pior valor BASE entre development e validation, e
percentual de folds sem trades é o maior valor BASE entre os dois períodos. STRESS é
completamente destrutivo quando esgota o capital, produz bankruptcy ou retorno de `-100%`. O
resultado sem os três melhores trades é fortemente negativo abaixo de `-1%` do capital inicial;
bootstrap é fortemente negativo somente em `NEGATIVE_UNCERTAIN`, quando o limite superior do
intervalo de total PnL fica abaixo de zero.

`NO_DEVELOPMENT_HYPOTHESIS`, `TOO_RESTRICTIVE` e `INSUFFICIENT_SAMPLE` preservam diretamente os
resultados de seleção e suficiência. Entre configurações operacionalmente viáveis e bloqueadas,
uma falha substantiva de retorno, drawdown, custo, concentração, liquidação, período ou lock é
`NOT_PROMISING`; evidência que não é negativa, mas permanece inconclusiva, é
`OPERATIONALLY_VIABLE_BUT_UNPROVEN`. Somente a aprovação de todos os critérios produz
`PROMISING_FOR_CONFIRMATION`.

Esses status são descrições de pesquisa. Nenhum habilita produção, paper trading ou candidata.

## Funil e traces

O funil sequencial registra `daily candles`, `warmup complete`, `SMA macro filter`,
`Donchian breakout`, `signal`, `risk sizing`, `risk approved`, `execution`, `position`,
`exit condition` e `closed trade`, separado para Spot long, Futures long, Futures short e Futures
long-short.

O trace mínimo contém data, close, SMA 200, high/low Donchian 20 anteriores, canal de saída, lado
macro, breakouts long/short, lado da posição, modo e percentual de risco, orçamento, stop inicial,
risco por unidade, quantidade e reason code. O trace documenta a decisão observável em cada etapa;
não inclui informação futura.

## Artefatos

Cada execução escreve em `reports/research/<trend-following-experiment-id>/`:

- `experiment_manifest.json`;
- `aggregation_integrity.json`;
- `daily_dataset_hashes.json`;
- `hypothesis_catalog.json`;
- `trend_following_decision_funnel.csv`;
- `trend_following_decision_traces.csv`;
- `development_results.csv`;
- `development_walk_forward.csv`;
- `operational_viability.json`;
- `development_selection.json`;
- `trend_following_validation_lock.json`;
- `validation_results.csv`;
- `validation_walk_forward.csv`;
- `defensive_risk_comparison.csv`;
- `trend_following_cost_scenarios.csv`;
- `trend_following_funding_impact.csv`;
- `side_contribution.csv`;
- `concentration_analysis.csv`;
- `bootstrap_uncertainty.json`;
- `hypothesis_assessment.json`;
- `future_confirmation_plan.json`;
- `trend_following_report.md`.

`trend_following_report.md` mantém exatamente esta ordem:

1. hipótese;
2. SMA 200;
3. Donchian 20;
4. saídas 10 e 20;
5. agregação diária;
6. point-in-time;
7. risco de 1%;
8. risco defensivo;
9. funil;
10. Spot;
11. Futures long;
12. Futures short;
13. Futures long-short;
14. development;
15. seleção;
16. lock;
17. validation;
18. custos;
19. funding;
20. drawdown;
21. comparação defensiva;
22. bootstrap;
23. classificação;
24. limitações;
25. próximo passo.

O manifest registra duração, contagens horárias e diárias, dias incompletos, hashes, warmup,
período efetivo, exclusão consumida, leverage, ausência de rede/ordens e identidade do código.

## CLI e garantias operacionais

Execução pré-registrada:

```bash
adaptive-trader research trend-following run \
  --symbol ETHUSDT \
  --source-interval 1h \
  --strategy-interval 1d \
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

adaptive-trader research trend-following show \
  --experiment reports/research/<experiment-id>
```

Nesta sprint, source diferente de `1h`, strategy interval diferente de `1d`, leverage diferente de
`1` ou períodos divergentes falham antes de carregar dados. A execução é totalmente offline:
consulta somente armazenamento local, não abre rede, não baixa dados, não autentica e não envia
ordens.

## Plano de confirmação futura

Se houver `PROMISING_FOR_CONFIRMATION`, `future_confirmation_plan.json` cria somente um plano,
nunca uma execução. A confirmação só pode começar depois de `2026-07-01T00:00:00Z`, precisa de no
mínimo 180 dias e 10 trades fechados, e preserva a configuração sem ajustes. Qualquer alteração
cria uma nova versão experimental. Paper trading continua desabilitado. Sem configuração
promissora, o status é `NO_CONFIRMATION_PLAN`.

Não há candidate freeze nesta sprint.

## Limitações

Candles diários OHLC não revelam a trajetória intradiária completa. Custos, spread, slippage,
margem, manutenção e liquidação são modelos; mark e funding históricos não reproduzem fila,
liquidez, impacto, partial fills, latência de rede, tiers, ADL ou insurance fund. A amostra diária
pode produzir poucos trades, e development/validation cobrem regimes históricos limitados.

Resultados históricos, inclusive uma eventual classificação promissora, não garantem desempenho
futuro e não constituem recomendação financeira.
