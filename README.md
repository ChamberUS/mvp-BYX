# Adaptive Trader

Primeira versão de um núcleo determinístico para pesquisa, backtest e paper trading de `ETHUSDT` em mercado spot. O projeto não envia ordens reais, não integra Binance, não usa futuros, margem, alavancagem ou modelos de IA.

## Arquitetura

O fluxo é intencionalmente separado:

1. `market_data` fornece candles.
2. Um `MarketContext` temporário é montado para uma análise e descartado ao final dela.
3. `strategy` recebe esse contexto e retorna somente `MarketSignal`.
4. `risk` avalia sinal, portfólio e limites; uma aprovação cria `OrderIntent`.
5. `execution` recebe somente `OrderIntent` e nesta versão produz `SimulatedOrder` preenchida localmente.
6. `storage` grava os dados permanentes para auditoria.

Dados permanentes são candles, sinais, decisões, ordens simuladas, fills, posições e snapshots. Eles são persistidos em SQLite e não são usados como substituto do contexto temporário, que é recriado a cada análise.

Os contratos em `domain/protocols.py` permitem trocar fontes de mercado, analisadores, risco, executor, repositório e relógio sem acoplar estratégia à execução.

## Instalação

Requer Python `3.12+`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

As configurações podem ser definidas no ambiente. Use `.env.example` como referência; o sistema não carrega nem armazena segredos.

## CLI

```bash
adaptive-trader doctor
adaptive-trader config show
adaptive-trader db init
adaptive-trader db status
```

O `doctor` valida Python, configuração, diretório do SQLite, conexão local e o modo research-only. `trading_enabled` permanece `false` por padrão e qualquer tentativa de ativar alavancagem, margem, futuros ou average down é rejeitada.

## Qualidade

```bash
ruff check .
mypy src
pytest
```

Os testes cobrem serialização sem conversão de `Decimal` para `float`, invariantes de configuração, criação do schema, gates de risco e o contrato que impede `MarketSignal` de chegar diretamente ao executor.

## Limitações e segurança

Esta sprint não implementa Binance Spot Testnet, qualquer corretora, backtest runner completo, interface web, IA, credenciais ou operações reais. O executor disponível é explicitamente simulado. Mesmo em paper trading, os resultados não são uma recomendação financeira; a próxima integração deve manter a aprovação de risco como pré-condição e adicionar testes de contrato antes de qualquer conexão externa.
