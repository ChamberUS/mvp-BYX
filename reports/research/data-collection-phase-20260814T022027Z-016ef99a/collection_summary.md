# Sprint 4A.3.3 — Data Collection Phase checkpoint

## Resultado operacional

A campanha `ethusdt-futures-intraday-discovery-v1` avançou de 117,777 para 1.952,860 segundos
científicos válidos: ganho real de 1.835,083 segundos. Isso equivale a 0,542461 hora científica,
ou 2,26025% do gate de 24 horas. As duas datas UTC formais continuam presentes, mas a cobertura é
fortemente desigual: 59,016 s em 2026-08-07 e 1.893,844 s em 2026-08-14.

Foram iniciadas e fechadas com segurança duas sessões novas. O primeiro chunk recebeu 1.800 s e
produziu 1.798,923 s científicos; o comando resumível então iniciou automaticamente outro chunk
completo. Como o ambiente não poderia permanecer conectado por 24 h, esse segundo chunk foi
encerrado com `SIGINT` e produziu 36,160 s científicos. Os dois manifests ficaram `COMPLETE`, os
arquivos foram fechados, os hashes persistidos, o replay foi determinístico e ambos foram
admitidos pelos gates existentes. Nenhuma duração solicitada foi usada como duração científica.

## Sessões, eventos e qualidade

O estado final possui cinco sessões operacionais: quatro admitidas e uma rejeitada. A sessão
histórica `microstructure-20260807T072555Z-usd_m_futures` permanece rejeitada por
`BOOK_NOT_SYNCHRONIZED|PROVENANCE_INCOMPLETE`; seu raw não foi reescrito nem copiado.

As quatro sessões científicas somam 306.138 eventos de sessão. A entrega dos quatro streams soma
306.126 eventos — 7.836 `aggTrade`, 277.221 `bookTicker`, 19.117 `depth` e 1.952 `markPrice` — e
os 12 eventos restantes são eventos internos de conexão/snapshot. O total operacional, incluindo
a sessão rejeitada, é 316.347 eventos de sessão.

As duas sessões novas têm commit completo `80c2bd48b3d8b4b197845174afee6e1f336e4177`, branch
`main`, worktree limpa e config hash
`8a6f459323958517c345c82418f2c16d9e293f4c351d41aa4a9ce6091b561dec`. Ambas terminaram com
zero gaps reais, drops, erros de parser, resyncs, disconnects ou incidentes não resolvidos; o book
final estava `SYNCHRONIZED`. O chunk longo registrou dois incidentes recuperados, em `depth` e
`markPrice`, classificados como `THRESHOLD_TOO_STRICT`; nenhum causou gap, resync ou invalidação
do book. A admissão resultante foi `ADMITTED`, sem exceção manual.

Os SHA-256 dos quatro novos arquivos gzip foram recalculados no disco e coincidem exatamente com
os hashes dos manifests. O campaign hash operacional passou de
`77eda2a3be2d42fc43021139145b69a3e034e25c01cc33664caa4d09486e63f3` para
`016ef99a5001b5468e0d42915ecaf5e7d04a5fa2179f3e120a4b458931411963`.

## Storage e integridade científica

O raw acumulado ocupa 133,090 MiB e permanece ignorado pelo Git; nenhum arquivo raw é rastreado.
Há 150,257 GiB livres. Pela taxa observada nas novas sessões, uma captura de 24 h projetaria cerca
de 2,323 GiB, portanto não há risco atual de espaço. Nenhum raw foi apagado, recomprimido ou
publicado.

Existem quatro `CAPTURE_BREAKS` operacionais no campaign manifest e três breaks entre as quatro
sessões admitidas. Eles não são market-data gaps e não foram preenchidos ou interpolados.

## Estado científico e próximo gate

`DATASET_STATUS` permanece `ENGINEERING_ONLY`; o estado desta fase é
`DATA_COLLECTION_IN_PROGRESS`. Faltam exatamente 84.447,140 segundos científicos para 86.400 s.
O snapshot `DISCOVERY_READY` não foi criado, discovery/confirmation não foram abertos e o holdout
não foi acessado. Nenhum economics, side, policy, notional, feature bin, horizonte ou runner foi
inspecionado para seleção. A resposta científica continua **MORE_DATA_REQUIRED**.

Retome a partir de uma worktree limpa:

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
