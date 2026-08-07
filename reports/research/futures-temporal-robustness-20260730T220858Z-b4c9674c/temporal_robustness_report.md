# Futures 1x temporal robustness

## 1. Objetivo

Diagnosticar não-estacionariedade nas seis configurações fixadas na Sprint 3A.5. Nenhuma
configuração é selecionada, congelada ou aprovada.

## 2. Dataset

ETHUSDT USD-M Futures 1h local, de 2022-01-01T00:00:00+00:00 a
2025-12-31T23:00:00+00:00, readiness `READY`.

## 3. Hashes

Combined dataset hash: `b4c9674c45ef10c96b68a72d84790aedfe6b93f638f23c63d4612ec61b6c570a`.

## 4. Configurações fixas

Somente as seis variantes 1x pré-registradas foram executadas. Estratégia, indicadores, stops,
funding, custos base e prioridade de eventos não foram alterados.

## 5. Decomposição anual

| Configuração | Ano | Retorno % | Trades |
|---|---:|---:|---:|
| FUTURES_LONG_BASELINE_1X | 2022 | -3.146994471590809505808094876 | 13 |
| FUTURES_LONG_BASELINE_1X | 2023 | -0.9082122044430904561014714339 | 4 |
| FUTURES_LONG_BASELINE_1X | 2024 | 1.428261338495189848789677735 | 4 |
| FUTURES_LONG_BASELINE_1X | 2025 | 3.191130996791833010173534274 | 12 |
| FUTURES_SHORT_MIRRORED_1X | 2022 | 2.752777871850451747209249300 | 39 |
| FUTURES_SHORT_MIRRORED_1X | 2023 | -1.460420688968945904200125649 | 2 |
| FUTURES_SHORT_MIRRORED_1X | 2024 | -7.168934217935247053883719127 | 19 |
| FUTURES_SHORT_MIRRORED_1X | 2025 | 3.345663510911517954141009062 | 21 |
| FUTURES_LONG_SHORT_BASELINE_1X | 2022 | -0.4808463801185397441838159658 | 52 |
| FUTURES_LONG_SHORT_BASELINE_1X | 2023 | -2.334410878939855477665805814 | 6 |
| FUTURES_LONG_SHORT_BASELINE_1X | 2024 | -5.533890671798695372116827012 | 23 |
| FUTURES_LONG_SHORT_BASELINE_1X | 2025 | 6.368141030701691465323751786 | 33 |
| FUTURES_LONG_SHORT_TIME_EXIT_12_1X | 2022 | -5.555827394811253011811893178 | 65 |
| FUTURES_LONG_SHORT_TIME_EXIT_12_1X | 2023 | -0.9088002788502822494444285771 | 7 |
| FUTURES_LONG_SHORT_TIME_EXIT_12_1X | 2024 | -4.548945359464863651306146281 | 30 |
| FUTURES_LONG_SHORT_TIME_EXIT_12_1X | 2025 | 9.971850163272186532339649990 | 42 |
| FUTURES_LONG_SHORT_TIME_EXIT_24_1X | 2022 | -3.517565037263877354883935520 | 55 |
| FUTURES_LONG_SHORT_TIME_EXIT_24_1X | 2023 | -2.263178874203505582686425544 | 6 |
| FUTURES_LONG_SHORT_TIME_EXIT_24_1X | 2024 | -3.445855337893695060503330887 | 25 |
| FUTURES_LONG_SHORT_TIME_EXIT_24_1X | 2025 | 6.833613305206798702237495369 | 36 |
| FUTURES_LONG_SHORT_TARGET_R_2_5_1X | 2022 | -3.431731077329327469886369956 | 44 |
| FUTURES_LONG_SHORT_TARGET_R_2_5_1X | 2023 | -1.986244244219674815631949324 | 6 |
| FUTURES_LONG_SHORT_TARGET_R_2_5_1X | 2024 | -4.182815079752971378694772457 | 24 |
| FUTURES_LONG_SHORT_TARGET_R_2_5_1X | 2025 | 11.95446227619742032882653467 | 30 |

## 6. Decomposição trimestral

Trades são atribuídos pelo timestamp de saída. Warmup e contexto anterior não entram nas métricas.

## 7. Janelas móveis

Foram avaliadas janelas 90/30, 180/60 e 365/90 sem uso de candles futuros.

## 8. Desenhos walk-forward

Rolling, expanding, rolling 730 dias e validation 180 dias usam parâmetros fixos e não ranqueiam
desenhos.

## 9. Fronteiras

As quatro fronteiras são descritivas. `BOUNDARY_SENSITIVE` indica mudança de padrão de sinal.

## 10. Leave-one-year-out

`SINGLE_YEAR_DEPENDENCE` indica mudança de sinal ao remover um ano.

## 11. Regimes

Regimes são point-in-time. `HIGH_VOLATILITY` usa o filtro ATR relativo já existente, sem
classificador treinado.

## 12. Transições

MFE, MAE, holding e saída são diagnósticos pós-evento e nunca alimentam a estratégia.

## 13. Volatilidade

Quantis foram calculados exclusivamente em 2022-2024 e aplicados sem recalibração a 2025.

## 14. Contexto

Retornos 24h/7d/30d, distância e slope da EMA longa e persistência são agrupamentos
pós-backtest, não filtros de entrada.

## 15. Long versus short

Contribuições brutas, custos, funding e PnL líquido permanecem separadas por lado.

## 16. Funding

Funding real permanece habilitado. Funding-off é somente diagnóstico e não participa de seleção.

## 17. Custos

LOW, BASE, HIGH e STRESS são os cenários fixos da Sprint 3A.5.

## 18. Concentração

São reportados best/top 3/top 5 e resultados após remover esses trades.

## 19. Bootstrap

Bootstrap pós-backtest usa seed 42 e
2000 iterações. Candles não são reordenados.

## 20. Scorecard

| Configuração | Estabilidade |
|---|---|
| FUTURES_LONG_BASELINE_1X | UNSTABLE |
| FUTURES_SHORT_MIRRORED_1X | UNSTABLE |
| FUTURES_LONG_SHORT_BASELINE_1X | UNSTABLE |
| FUTURES_LONG_SHORT_TIME_EXIT_12_1X | UNSTABLE |
| FUTURES_LONG_SHORT_TIME_EXIT_24_1X | UNSTABLE |
| FUTURES_LONG_SHORT_TARGET_R_2_5_1X | UNSTABLE |

## 21. Explicação de 2025

As associações são quantitativas e não causais. O artefato específico distingue padrões repetidos
de resultados não observados anteriormente.

## 22. Classificação final

| Configuração | Classificação | Fundamentação |
|---|---|---|
| FUTURES_LONG_BASELINE_1X | NON_STATIONARY | 2025 is positive without aggregate support in 2022-2024 |
| FUTURES_SHORT_MIRRORED_1X | NON_STATIONARY | 2025 is positive without aggregate support in 2022-2024 |
| FUTURES_LONG_SHORT_BASELINE_1X | NON_STATIONARY | 2025 is positive without aggregate support in 2022-2024 |
| FUTURES_LONG_SHORT_TIME_EXIT_12_1X | NON_STATIONARY | 2025 is positive without aggregate support in 2022-2024 |
| FUTURES_LONG_SHORT_TIME_EXIT_24_1X | NON_STATIONARY | 2025 is positive without aggregate support in 2022-2024 |
| FUTURES_LONG_SHORT_TARGET_R_2_5_1X | NON_STATIONARY | 2025 is positive without aggregate support in 2022-2024 |

Nenhuma classificação habilita leverage, paper trading, Testnet ou produção.

## 23. Limitações

OHLC não contém caminho intrabar, livro, fila, impacto, partial fills ou causalidade. Agregação
temporal por saída pode atribuir a um período trades iniciados anteriormente.

## 24. Próximo passo

Revisar os diagnósticos sem ajustar parâmetros ou consumir 2026. Uma hipótese futura exige nova
pré-especificação; não é candidata desta sprint.

## Warnings

- BOUNDARY_SENSITIVE
- SINGLE_YEAR_DEPENDENCE
- STRESS_COLLAPSE
- LOW_COST_ONLY_EDGE
- COST_DOMINATED_PERIOD
- RESULT_DEPENDS_ON_FEW_TRADES
- POST_EVENT_DIAGNOSTICS_ONLY
- NO_CAUSAL_INTERPRETATION
- NO_CANDIDATE_FREEZE
