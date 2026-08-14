# Sprint 4A.3.3 — Data Collection Phase continuation

## Progresso científico

A continuação partiu de 3.782,251 s científicos e terminou com 5.624,936 s: foram adicionados
1.842,685 s válidos. A campanha agora contém 1,562482 h científicas, oito sessões admitidas e uma
sessão histórica rejeitada. O checkpoint de 6 h ainda não foi atingido; faltam 15.975,064 s. Para
12 h faltam 37.575,064 s, para 18 h faltam 59.175,064 s e para o gate de 24 h faltam 80.775,064 s.

As duas sessões novas foram capturadas em `main` limpo no commit
`f9526aeb00a565a51dba3bcb139d67c7ac7d6a1c`, com config operacional
`8a6f459323958517c345c82418f2c16d9e293f4c351d41aa4a9ce6091b561dec`. O chunk completo
contribuiu 1.798,255 s e 240.926 eventos. Quando o comando iniciou automaticamente o próximo
chunk, ele foi fechado com `SIGINT` seguro e contribuiu 44,430 s e 2.646 eventos. Ambos ficaram
`COMPLETE` e `ADMITTED`.

## Qualidade e distribuição

As oito sessões científicas somam 823.399 eventos de sessão. A entrega dos quatro streams contém
23.721 `aggTrade`, 738.964 `bookTicker`, 55.065 `depth` e 5.625 `markPrice`; 24 eventos adicionais
são internos de conexão/snapshot. As novas sessões tiveram zero gaps, drops, parser errors,
resyncs, disconnects e incidentes não resolvidos; terminaram com book `SYNCHRONIZED`, runtime
`READY` e replay determinístico.

O chunk longo registrou 14 incidentes recuperados: nove `THRESHOLD_TOO_STRICT` e cinco
`NORMAL_NO_UPDATE`, distribuídos em 11 de depth e três de markPrice. Nenhum causou gap, resync ou
book inválido. A sessão curta não teve incidentes. Os thresholds de liveness permaneceram
congelados e a admissão automática não foi alterada.

O formal date gate contém 2026-08-07 e 2026-08-14. A cobertura continua concentrada:
2026-08-07 contribui 59,016 s (0,016393 h), enquanto 2026-08-14 contribui 5.565,920 s
(1,546089 h). Isso permanece diagnóstico, não gate novo.

Os 15 arquivos de eventos do campaign foram verificados novamente por SHA-256 e todos coincidem
com seus manifests. A sessão histórica rejeitada continua preservada e rejeitada por
`BOOK_NOT_SYNCHRONIZED|PROVENANCE_INCOMPLETE`. Há oito `CAPTURE_BREAKS` operacionais e sete entre
sessões científicas; nenhum foi preenchido ou atravessado.

## Storage e decisão da fase

O raw ocupa 233.689.088 bytes (222,863 MiB; 0,217640 GiB), aumento de 44.793.856 bytes, e há
148,836 GiB livres. A taxa comprimida desta continuação projeta aproximadamente 1,924 GiB para
24 h; não há risco de storage. Raw continua ignorado e não foi destruído, recomprimido ou
versionado.

O campaign hash final é
`4e8a01d441a24fedc268d16f2fca20f603340048371a595990bdf62248d5c431`. O estado permanece
`DATA_COLLECTION_IN_PROGRESS`, dataset `ENGINEERING_ONLY`, `DISCOVERY_READY=false` e resposta
**MORE_DATA_REQUIRED**. Nenhum economics foi executado, nenhuma métrica financeira foi
inspecionada, nenhum vencedor foi selecionado e o holdout continuou fechado.

Os quality gates passaram: Ruff, mypy em 135 arquivos, 594 testes integrais, cobertura de 81,48%,
doctor research-only/sem credenciais, validação JSON/CSV e `git diff --check`. Não houve mudança
funcional de código nem teste novo.

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
