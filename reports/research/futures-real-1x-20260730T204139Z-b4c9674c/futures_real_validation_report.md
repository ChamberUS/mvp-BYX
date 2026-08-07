# Validação real USD-M Futures 1x

## 1. Fontes públicas

Somente `GET /fapi/v1/klines`, `GET /fapi/v1/markPriceKlines` e
`GET /fapi/v1/fundingRate`, sem autenticação.

## 2. Períodos

- Development: 2022-01-01 00:00:00+00:00 a 2024-12-31 23:00:00+00:00
- Validation: 2025-01-01 00:00:00+00:00 a 2025-12-31 23:00:00+00:00

## 3. Exclusão de 2026

O período consumido 2026-01-01 00:00:00+00:00 a
2026-07-01 00:00:00+00:00 foi somente registrado e não foi baixado, carregado ou usado.

## 4. Integridade dos candles

35064 candles fechados; 0 duplicatas;
0 gaps; hash `fe2e3e11d5fd62da0d96ea48d1985b379b2bd8ed427af58212b4e49ec404c38f`.

## 5. Integridade do mark

Cobertura 100%; exact=35064;
previous=0; missing=0;
future=0. Nunca há busca nearest bidirecional.

## 6. Integridade do funding

4383 eventos; cobertura 100%;
missing windows=0; hash `b8936cf7f526e0b2766f04998dc3a282e6a5e421509fe954d1b26b3262570da8`.

## 7. Hashes

Combined dataset hash: `b4c9674c45ef10c96b68a72d84790aedfe6b93f638f23c63d4612ec61b6c570a`.

## 8. Gaps

Política `WARN`; nenhum candle foi fabricado, interpolado ou preenchido silenciosamente.

## 9. Configurações fixas

As seis variantes em `predefined_futures_variants.json` foram executadas em 1x sem seleção
adaptativa.

## 10. Long

Resultados estão em `segment_results.csv` e `walk_forward_results.csv`.

## 11. Short

O short é a regra espelhada já existente; nenhuma regra foi ajustada após observar validation.

## 12. Long-short

Apenas uma posição isolada por vez, sem hedge simultâneo.

## 13. Walk-forward

Rolling 365/90/90, separado entre development e validation.

## 14. Custos

LOW, BASE, HIGH e STRESS alteram apenas custos de execução; funding real permanece inalterado.

## 15. Funding

`FUNDING_DISABLED_EXPLICITLY` é diagnóstico não elegível para assessment e emite
`FUNDING_DISABLED_DIAGNOSTIC_ONLY`.

## 16. Liquidações

Foram registradas 0 liquidações. O modelo é aproximado,
`LIQUIDATION_FIRST`, seguido de `STOP_FIRST`.

## 17. Benchmarks

CASH, SPOT_BUY_AND_HOLD, FUTURES_LONG_1X e FUTURES_SHORT_1X são descritivos e não selecionáveis.

## 18. Comparação Spot

O checkpoint Spot conhecido permanece separado em `spot_futures_1x_comparison.*`.

## 19. Critérios

Todos os limites pré-registrados foram avaliados conjuntamente; retorno positivo isolado não basta.

## 20. Classificação

- FUTURES_LONG_BASELINE_1X: **NOT_PROMISING**
- FUTURES_SHORT_MIRRORED_1X: **NOT_PROMISING**
- FUTURES_LONG_SHORT_BASELINE_1X: **NOT_PROMISING**
- FUTURES_LONG_SHORT_TIME_EXIT_12_1X: **NOT_PROMISING**
- FUTURES_LONG_SHORT_TIME_EXIT_24_1X: **NOT_PROMISING**
- FUTURES_LONG_SHORT_TARGET_R_2_5_1X: **NOT_PROMISING**

Configurações PROMISING_FOR_FURTHER_VALIDATION: 0.

## 21. Limitações

OHLC não contém caminho intrabar, fila, profundidade, impacto, partial fills nem tiers completos de
manutenção. O estudo não demonstra lucratividade nem equivalência com execução real.

## 22. Próximos passos

Revisar integridade e consistência temporal. Não congelar candidata, habilitar paper trading,
executar 2x/3x ou consumir 2026 nesta sprint.

## Warnings

- LIQUIDATION_MODEL_APPROXIMATE
- MAINTENANCE_MARGIN_APPROXIMATE
- FUNDING_DISABLED_DIAGNOSTIC_ONLY

## Correções necessárias para dados reais

O histórico público retornou `markPrice=""` em funding. O parser passou a tratar esse campo
opcional como ausente. Quando não há mark no evento, o cálculo usa `mark.open` conhecido da hora,
nunca `mark.close` futuro. Um relatório intermediário com o fallback antigo foi invalidado antes
desta entrega.

As linhas descritivas de benchmark são emitidas uma única vez por período e benchmark.
