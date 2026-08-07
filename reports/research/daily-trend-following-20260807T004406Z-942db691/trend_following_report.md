# Daily Trend Following — Sprint 3C.1

## 1. Hipótese

Pesquisa offline e pré-registrada de trend following diário em ETHUSDT. Os
resultados não autorizam produção e não são declaração de lucratividade.

## 2. SMA 200

O filtro macro usa exatamente 200 fechamentos diários terminando no dia da
decisão. Os primeiros 199 candles são somente warmup.

## 3. Donchian 20

Entradas usam fechamento confirmado além do canal de 20 dias anteriores.

## 4. Saídas 10 e 20

Somente os dois canais pré-registrados foram executados. O candle corrente é
excluído de todos os canais.

## 5. Agregação diária

Candles 1h locais foram agregados em UTC sem preenchimento. Dias incompletos
observados: 1; a política de pesquisa foi `WARN_AND_EXCLUDE`.

## 6. Point-in-time

Sinais são confirmados no fechamento diário e executados apenas na primeira
abertura 1h elegível do dia UTC seguinte.

## 7. Risco de 1%

O orçamento máximo normal é 1% do equity; o stop Donchian inicial é referência
de sizing, não stop intraday.

## 8. Risco defensivo

Três perdas estruturais consecutivas reduzem o orçamento para 0,5%. O modo
normal só retorna quando o equity alcança o nível anterior à sequência.

## 9. Funil

O funil e os traces preservam candle diário, warmup, filtro macro, breakout,
sizing, aprovação, execução, posição e saída.

## 10. Spot

Spot foi executado estritamente long-only, com caixa e custos explícitos.

## 11. Futures long

Futures long permaneceu isolado, 1x, com mark e funding horários.

## 12. Futures short

Short é uma direção Futures explícita e nunca reutiliza `SELL` Spot.

## 13. Futures long-short

O modo long-short mantém no máximo uma posição e não faz hedge simultâneo.

## 14. Development

Somente 2022–2023 participou da avaliação e da seleção.

## 15. Seleção

- SPOT/LONG: none (NO_DEVELOPMENT_HYPOTHESIS)
- FUTURES/LONG: none (NO_DEVELOPMENT_HYPOTHESIS)
- FUTURES/SHORT: none (NO_DEVELOPMENT_HYPOTHESIS)
- FUTURES/LONG_SHORT: none (NO_DEVELOPMENT_HYPOTHESIS)

## 16. Lock

O lock foi gravado antes de consultar 2024 e permaneceu byte a byte imutável.
Commit inicial registrado: `a889362effe0745ac06ce42ae82cadf16a91bdee`.

## 17. Validation

2024 executou somente benchmarks e configurações bloqueadas; não selecionou nem
alterou parâmetros.

## 18. Custos

LOW, BASE, HIGH e STRESS foram reportados. BASE foi o único cenário elegível
para seleção.

## 19. Funding

Funding histórico foi aplicado nos timestamps reais e sua fonte permaneceu
igual entre cenários.

## 20. Drawdown

Drawdown, retorno/drawdown, maior perda e tempo defensivo foram registrados sem
assumir benefício da redução de risco.

## 21. Comparação defensiva

Pares equivalentes de saída 10 e 20 com risco fixo e defensivo foram comparados
separadamente.

## 22. Bootstrap

Somente trades fechados foram reamostrados, seed 42, 2.000 iterações e intervalo
percentil de 95%.

## 23. Classificação

Configurações promissoras apenas para confirmação futura: 0.
Nenhuma candidata foi congelada e nenhuma classificação habilita produção.

## 24. Limitações

OHLC diário, custos simulados, mark/funding históricos e amostra pequena não
demonstram causalidade nem desempenho futuro.

## 25. Próximo passo

O plano de confirmação, quando aplicável, é apenas documental e começa depois
de 2026-07-01, por no mínimo 180 dias e 10 trades fechados, sem ajuste.

2025 e 2026 não foram carregados. Leverage permaneceu 1x. Não houve rede,
download, autenticação, Testnet, paper trading, API privada ou ordem externa.
