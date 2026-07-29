# Backtest assumptions

- A fonte prevista é a API pública de klines da Binance Spot; nenhum endpoint autenticado é usado.
- Apenas candles fechados entram no contexto e no backtest por padrão.
- A estratégia recebe somente candles com abertura menor ou igual ao instante analisado.
- Uma decisão no fechamento do candle `T` é executada na abertura de `T+1`.
- Compras usam preço de referência mais spread e slippage; vendas usam preço de referência menos ambos.
- Spread e slippage são custos simulados em basis points, não cotações oficiais universais.
- Taxas são simuladas em basis points e debitadas do caixa.
- Stop loss e take profit usam níveis já aprovados pelo risco.
- Se stop e alvo forem atingidos pelo mesmo OHLC, `STOP_FIRST` é aplicado e o relatório marca `intrabar_ambiguous=true`.
- Uma posição aberta no fim é fechada no último fechamento quando `force_close_at_end=true`.
- O modelo não simula livro de ofertas, liquidez, partial fills, latência de rede ou impacto de mercado detalhado.
- OHLC não revela a ordem intrabar; por isso o resultado não escolhe o melhor caminho.
- A execução é local e simulada; nenhum dinheiro ou ordem real é movimentado.
- Resultados passados não garantem resultados futuros.
