# Sprint 4A.3.3 — Data Collection Phase continuation

## Progresso científico

A continuação partiu de 1.952,860 s científicos e terminou com 3.782,251 s: foram adicionados
1.829,391 s válidos. A campanha agora contém 1,050625 h científicas, seis sessões admitidas e uma
sessão histórica rejeitada. O checkpoint de 6 h ainda não foi atingido; faltam 17.817,749 s. Para
o gate de 24 h faltam 82.617,749 s.

As duas sessões novas foram capturadas em `main` limpo no commit
`33236a27908d97e4faf933753d4b73ec39d210d5`, com config operacional
`8a6f459323958517c345c82418f2c16d9e293f4c351d41aa4a9ce6091b561dec`. O chunk completo
contribuiu 1.798,969 s e 271.319 eventos. Quando o comando iniciou automaticamente o próximo
chunk, ele foi fechado com `SIGINT` seguro e contribuiu 30,422 s e 2.370 eventos. Ambos ficaram
`COMPLETE` e `ADMITTED`.

## Qualidade e distribuição

As seis sessões científicas somam 579.827 eventos de sessão. A entrega de streams contém 16.502
`aggTrade`, 522.497 `bookTicker`, 37.028 `depth` e 3.782 `markPrice`; 18 eventos adicionais são
internos de conexão/snapshot. As novas sessões tiveram zero gaps, drops, parser errors, resyncs,
disconnects e incidentes não resolvidos; terminaram com book `SYNCHRONIZED`, runtime `READY` e
replay determinístico.

O chunk longo registrou quatro incidentes recuperados: dois em depth e dois em markPrice. Três
foram `THRESHOLD_TOO_STRICT` e um foi `NORMAL_NO_UPDATE`. Nenhum causou gap, resync ou book
inválido. A sessão curta não teve incidentes.

O formal date gate contém 2026-08-07 e 2026-08-14. O diagnóstico de cobertura continua desigual:
2026-08-07 contribui 59,016 s (0,016393 h), enquanto 2026-08-14 contribui 3.723,235 s
(1,034232 h). Isso não cria um gate novo, mas precisa permanecer explícito para interpretação
futura.

Os 11 arquivos de eventos do campaign foram verificados novamente por SHA-256 e todos coincidem
com seus manifests. A sessão histórica rejeitada continua preservada e rejeitada por
`BOOK_NOT_SYNCHRONIZED|PROVENANCE_INCOMPLETE`. Há seis `CAPTURE_BREAKS` operacionais e cinco
entre sessões científicas; nenhum foi preenchido.

## Storage e decisão da fase

O raw ocupa 180,145 MiB, aumento de 49.340.416 bytes, e há 150,073 GiB livres. A taxa comprimida
observada nesta continuação projeta aproximadamente 2,149 GiB para 24 h; não há risco de storage.
Raw continua ignorado e não foi destruído, alterado ou versionado.

O campaign hash final é
`3eb387258276637bae94be4387f5b16cd76020c20c4c2ea11149af76530165eb`. O estado permanece
`DATA_COLLECTION_IN_PROGRESS`, dataset `ENGINEERING_ONLY`, `DISCOVERY_READY=false` e resposta
**MORE_DATA_REQUIRED**. Nenhum economics foi executado, nenhuma métrica financeira foi
inspecionada e o holdout continuou fechado.

## Resume

```bash
adaptive-trader market microstructure campaign-status \
  --campaign ethusdt-futures-intraday-discovery-v1

adaptive-trader market microstructure campaign-record \
  --market futures \
  --symbol ETHUSDT \
  --campaign-id ethusdt-futures-intraday-discovery-v1 \
  --streams aggTrade,bookTicker,depth,markPrice \
  --depth-speed 100ms \
  --chunk-seconds 1800 \
  --total-seconds 86400 \
  --output-dir data/microstructure
```
