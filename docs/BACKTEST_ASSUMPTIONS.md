# Backtest assumptions

- A fonte prevista é a API pública de klines da Binance Spot; nenhum endpoint autenticado é usado.
- Apenas candles fechados entram no contexto e no backtest por padrão.
- `evaluation_start_time` define o início solicitado por `open_time >= evaluation_start_time`; o início efetivo pode ser deslocado quando não há candles suficientes para maturar os indicadores.
- Candles de input anteriores ao início efetivo são warmup apenas para indicadores; não geram operações, snapshots financeiros ou pontos avaliados de equity/exposição.
- `BacktestResult.candle_count` é o número de candles avaliados; `input_candle_count` e `warmup_candle_count` registram o restante explicitamente.
- A estratégia recebe somente candles com abertura menor ou igual ao instante analisado.
- Uma decisão no fechamento do candle `T` é executada na abertura de `T + latency_candles`; `latency_candles >= 1` e execução no mesmo candle é inválida.
- O dia de negociação usa UTC. Na troca de data, `day_start_equity` recebe o patrimônio marcado no fechamento anterior e `entries_today`, `orders_today` e `closed_trades_today` são zerados.
- O limite de operações diárias conta apenas novas entradas BUY. Saídas SELL de posições existentes continuam permitidas mesmo após limite de entradas ou perda diária.
- A perda diária é `max(0, day_start_equity - current_equity)` e o limite percentual usa `day_start_equity`.
- Compras usam preço de referência mais spread e slippage; vendas usam preço de referência menos ambos.
- Spread e slippage são custos simulados em basis points, não cotações oficiais universais.
- Taxas são simuladas em basis points e debitadas do caixa.
- Uma compra só é executada se o custo efetivo quantizado, incluindo taxa, spread e slippage, couber no caixa; o caixa nunca pode ficar negativo.
- Stop loss e take profit usam níveis já aprovados pelo risco.
- Se stop e alvo forem atingidos pelo mesmo OHLC, `STOP_FIRST` é aplicado e o relatório marca `intrabar_ambiguous=true`.
- Stop e alvo são avaliados antes de qualquer saída por tempo; `TIME_EXIT` só usa o fechamento
  quando nenhuma proteção foi acionada no candle.
- Stop trailing e break-even são calculados somente depois do fechamento do candle e não podem retroagir sobre a mínima/máxima desse mesmo candle.
- `holding_candles` é a diferença entre o índice do candle de saída e o índice da entrada original, inclusive para saídas parciais e fechamento forçado.
- Uma posição aberta no fim é fechada no último fechamento quando `force_close_at_end=true`.
- O modelo não simula livro de ofertas, liquidez, partial fills, latência de rede ou impacto de mercado detalhado.
- OHLC não revela a ordem intrabar; por isso o resultado não escolhe o melhor caminho.
- A execução é local e simulada; nenhum dinheiro ou ordem real é movimentado.
- Resultados passados não garantem resultados futuros.

## USD-M Futures

- O motor Futures é independente do motor Spot; caixa Spot nunca é reutilizado como wallet ou
  margem Futures.
- Somente contratos perpétuos USD-M com margem `ISOLATED` são aceitos.
- Alavancagem é `Decimal`, começa em `1x` e não pode exceder `3x`.
- `notional = execution_price * quantity`; `initial_margin = notional / leverage`.
- A abertura exige margem inicial, taxa de entrada e buffer dentro do saldo disponível.
- PnL long é `(mark - entry) * quantity`; PnL short é `(entry - mark) * quantity`.
- Execução usa futures kline com spread/slippage; PnL não realizado, manutenção e liquidação usam
  mark price persistido separadamente.
- A manutenção usa `notional * maintenance_margin_rate`; a taxa fixa não reproduz tiers reais.
- Liquidação é aproximada por OHLC. Se liquidação e saída protetiva forem possíveis no mesmo
  candle, `LIQUIDATION_FIRST` é aplicada e a ambiguidade é registrada.
- Funding positivo é pago por long e recebido por short; funding negativo inverte os fluxos.
- Funding é aplicado apenas quando uma posição está aberta no timestamp do evento.
- Funding ausente usa `FAIL` por padrão. `WARN_AND_SKIP` e `DISABLE_EXPLICITLY` exigem escolha
  explícita.
- `force_close_at_end` fecha a posição local no último candle; isso não representa ordem real.
- O relatório registra alavancagem, notional, margem, utilização, funding, taxas, liquidações,
  drawdown, estado bankrupt/depleted e exposição long/short.
- Não há equivalência garantida com execução, tiers, ADL, insurance fund ou liquidação real da
  Binance.
