# Pullback Frequency Calibration — Sprint 3B.2

## 1. Problema observado

Retomadas detectadas na Sprint 3B.1 não chegavam a sinais. Esta auditoria é
somente pesquisa offline e não declara lucratividade.

## 2. Auditoria lógica

A revalidação do regime no candle de retomada era incompatível com o estado
point-in-time já estabelecido e redundante com persistência. O regime agora é
travado no início do pullback; cruzamento da EMA curta e fechamento direcional
possuem reason codes separados. Auditoria detalhada em `pullback_logic_audit.json`.

## 3. Funil

O funil registra a ordem real, contagens de entrada, aprovação e falha. Nenhuma
etapa posterior excede a anterior.

## 4. Retomadas rejeitadas

Foram registradas 70 retomadas rejeitadas. A
primeira falha dominante foi `PRICE_OVEREXTENDED`.

## 5. Ablação

Cada contrafactual remove exatamente uma regra e nunca executa sinal ou trade.

## 6. Catálogo

Hash canônico: `ebc240e4241134a5bce61bb05d9154f259cbecf72867c539e90b297d9b041258`.
SHA-256 do arquivo: `2c7227c8d8bc17492bea8d77f91cfed2a6e63beaea9609b731bcdc281529fc55`.
Oito definições fixas alteram no máximo uma dimensão em relação à base.

## 7. Suficiência operacional

Definições viáveis: 0. Viabilidade foi determinada sem retorno.

## 8. Seleção sem retorno

- SPOT/LONG: none
- FUTURES/LONG: none
- FUTURES/SHORT: none
- FUTURES/LONG_SHORT: none

## 9. Development financeiro

Somente definições selecionadas receberam resultados financeiros reportados.

## 10. Lock

O lock foi criado antes de carregar/executar validation e inclui parâmetros,
hashes, commit `a889362effe0745ac06ce42ae82cadf16a91bdee`, dataset e métricas de frequência.

## 11. Validation

2024 executou somente baseline e definições bloqueadas.

## 12. Pós-evento

`POST_EVENT_ONLY_NO_STRATEGY_ACCESS`. Retornos, MFE e MAE não foram acessados
pela estratégia e não alteraram o catálogo.

## 13. Classificação

As classificações são descritivas; nenhuma candidata foi congelada.

## 14. Limitações

OHLC, regime aproximado, custos simulados, mark/funding históricos e amostra
limitada não demonstram causalidade nem desempenho futuro.

## 15. Próximo passo

Não ampliar a busca nesta sprint. Qualquer novo teste requer pré-registro
separado.

2025 e 2026 não foram carregados. Leverage permaneceu 1x. Não houve rede,
download, autenticação, Testnet, paper trading ou ordem externa.
