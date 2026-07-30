# Diagnostics report

## Period and protection

- Experiment: `diagnose-d3e3653ff3-a004e854de`
- Dataset: `BINANCE-ETHUSDT-1h-d3e3653ff3f0deac`
- Dataset hash: `d3e3653ff3f0deac8b592133c138662fd69b15927f5adaf7136ac29b68c2adae`
- Consumed test data is not used for selection, ranking, sensitivity, or interval choice.
- This report is diagnostic and post-event; it is not a production approval.

## Decision funnel

- all_segments: {'scope': 'all_segments', 'total_candles_evaluated': 4242, 'eligible_after_warmup': 4242, 'trending_up': 73, 'ema_confirmed': 2043, 'volume_confirmed': 1504, 'volatility_acceptable': 4231, 'risk_reward_acceptable': 30, 'buy_signals': 30, 'risk_approved': 6, 'orders_executed': 6, 'closed_trades': 6, 'eligible_after_warmup_percent': Decimal('100'), 'trending_up_percent': Decimal('1.720886374351720886374351721'), 'ema_confirmed_percent': Decimal('48.16124469589816124469589816'), 'volume_confirmed_percent': Decimal('35.45497406883545497406883545'), 'volatility_acceptable_percent': Decimal('99.74068835454974068835454974'), 'risk_reward_acceptable_percent': Decimal('0.7072135785007072135785007072'), 'buy_signals_percent': Decimal('0.7072135785007072135785007072'), 'risk_approved_percent': Decimal('0.1414427157001414427157001414'), 'orders_executed_percent': Decimal('0.1414427157001414427157001414')}
- development: {'scope': 'development', 'total_candles_evaluated': 3374, 'eligible_after_warmup': 3374, 'trending_up': 64, 'ema_confirmed': 1599, 'volume_confirmed': 1185, 'volatility_acceptable': 3363, 'risk_reward_acceptable': 29, 'buy_signals': 29, 'risk_approved': 5, 'orders_executed': 5, 'closed_trades': 5, 'eligible_after_warmup_percent': Decimal('100'), 'trending_up_percent': Decimal('1.896858328393598103141671606'), 'ema_confirmed_percent': Decimal('47.39181979845880260818020154'), 'volume_confirmed_percent': Decimal('35.12151748666271487848251334'), 'volatility_acceptable_percent': Decimal('99.67397747480735032602252519'), 'risk_reward_acceptable_percent': Decimal('0.8595139300533491404860699467'), 'buy_signals_percent': Decimal('0.8595139300533491404860699467'), 'risk_approved_percent': Decimal('0.1481920569057498518079430943'), 'orders_executed_percent': Decimal('0.1481920569057498518079430943')}
- validation: {'scope': 'validation', 'total_candles_evaluated': 868, 'eligible_after_warmup': 868, 'trending_up': 9, 'ema_confirmed': 444, 'volume_confirmed': 319, 'volatility_acceptable': 868, 'risk_reward_acceptable': 1, 'buy_signals': 1, 'risk_approved': 1, 'orders_executed': 1, 'closed_trades': 1, 'eligible_after_warmup_percent': Decimal('100'), 'trending_up_percent': Decimal('1.036866359447004608294930876'), 'ema_confirmed_percent': Decimal('51.15207373271889400921658986'), 'volume_confirmed_percent': Decimal('36.75115207373271889400921659'), 'volatility_acceptable_percent': Decimal('100'), 'risk_reward_acceptable_percent': Decimal('0.1152073732718894009216589862'), 'buy_signals_percent': Decimal('0.1152073732718894009216589862'), 'risk_approved_percent': Decimal('0.1152073732718894009216589862'), 'orders_executed_percent': Decimal('0.1152073732718894009216589862')}

## HOLD reasons

Future returns in `hold_reason_analysis.csv` are calculated offline after traces were recorded;
they are never provided to the strategy.

- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 101, 'percent': Decimal('0.4795821462488129154795821462'), 'horizon_candles': 1, 'future_return_mean': Decimal('0.0009720658339877412572780828129'), 'maximum_favorable_movement_mean': Decimal('0.007164655102012135929715565819'), 'maximum_adverse_movement_mean': Decimal('-0.006385533761729128073106116326'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 101, 'percent': Decimal('0.4795821462488129154795821462'), 'horizon_candles': 3, 'future_return_mean': Decimal('0.003171512647037680890094488052'), 'maximum_favorable_movement_mean': Decimal('0.01489081145161226389511485061'), 'maximum_adverse_movement_mean': Decimal('-0.01032025562761422843176341145'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 101, 'percent': Decimal('0.4795821462488129154795821462'), 'horizon_candles': 6, 'future_return_mean': Decimal('0.004451326044099488974073169927'), 'maximum_favorable_movement_mean': Decimal('0.02168970444231871995642532165'), 'maximum_adverse_movement_mean': Decimal('-0.01414993097572088603102106576'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 101, 'percent': Decimal('0.4795821462488129154795821462'), 'horizon_candles': 12, 'future_return_mean': Decimal('0.004519512735768838776816219293'), 'maximum_favorable_movement_mean': Decimal('0.03079300086164753352097942202'), 'maximum_adverse_movement_mean': Decimal('-0.01955325737702142931920986807'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 101, 'percent': Decimal('0.4795821462488129154795821462'), 'horizon_candles': 24, 'future_return_mean': Decimal('0.002220160784819089293410041710'), 'maximum_favorable_movement_mean': Decimal('0.04357689599338990200462055592'), 'maximum_adverse_movement_mean': Decimal('-0.03442574346610901527037804462'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 4104, 'percent': Decimal('19.48717948717948717948717949'), 'horizon_candles': 1, 'future_return_mean': Decimal('-0.00008245049604262128429140970246'), 'maximum_favorable_movement_mean': Decimal('0.005629408757826414505400599557'), 'maximum_adverse_movement_mean': Decimal('-0.005855062200089023566757598767'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 4104, 'percent': Decimal('19.48717948717948717948717949'), 'horizon_candles': 3, 'future_return_mean': Decimal('-0.0002599270366258588725624817161'), 'maximum_favorable_movement_mean': Decimal('0.009806417967303182857973457510'), 'maximum_adverse_movement_mean': Decimal('-0.01066564803947031868136970117'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 4104, 'percent': Decimal('19.48717948717948717948717949'), 'horizon_candles': 6, 'future_return_mean': Decimal('-0.0004984351869488549991424666094'), 'maximum_favorable_movement_mean': Decimal('0.01406712371443568690349118443'), 'maximum_adverse_movement_mean': Decimal('-0.01567526969276622364391619521'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 4104, 'percent': Decimal('19.48717948717948717948717949'), 'horizon_candles': 12, 'future_return_mean': Decimal('-0.0009213130918923885430201364860'), 'maximum_favorable_movement_mean': Decimal('0.02037752345795634346845574924'), 'maximum_adverse_movement_mean': Decimal('-0.02323801721906310158587253960'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 4104, 'percent': Decimal('19.48717948717948717948717949'), 'horizon_candles': 24, 'future_return_mean': Decimal('-0.001431643751420941949854651003'), 'maximum_favorable_movement_mean': Decimal('0.02959140696061557629753412501'), 'maximum_adverse_movement_mean': Decimal('-0.03398821384405289619289547775'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}

## Entries

- Entry diagnostic rows: 0

## Exits

- None

## Entry and exit decomposition

- Scenario rows: 32
- Artifact: `entry_exit_decomposition.csv`

## Cost scenarios by fold

- Rows: 12
- Artifact: `cost_scenarios_by_fold.csv`

## Detailed regimes

- Rows: 6
- Artifact: `detailed_regime_metrics.csv`

## Timeframe comparison

- Rows: 0
- When zero, timeframe comparison is not applicable to this command and the CSV contains
  only its valid status header. Missing intervals are never downloaded automatically.

## OFAT sensitivity

- Rows: 38
- Only one configured strategy parameter changes per scenario.

## Robustness scorecard

- {'dimension': 'net_return', 'classification': 'GOOD', 'justification': 'net return across completed segments'}
- {'dimension': 'drawdown', 'classification': 'GOOD', 'justification': 'worst drawdown=0.2816075314416241566944982912%'}
- {'dimension': 'trade_sample', 'classification': 'INCONCLUSIVE', 'justification': 'closed trades=6'}
- {'dimension': 'fold_consistency', 'classification': 'GOOD', 'justification': 'positive folds=1/2'}
- {'dimension': 'cost_resilience', 'classification': 'INCONCLUSIVE', 'justification': 'cost scenarios must be inspected separately'}
- {'dimension': 'parameter_stability', 'classification': 'INCONCLUSIVE', 'justification': 'no parameter selection was performed'}
- {'dimension': 'regime_dependence', 'classification': 'INCONCLUSIVE', 'justification': 'regime sample is diagnostic, not causal'}
- {'dimension': 'benchmark_comparison', 'classification': 'MIXED', 'justification': 'BUY_AND_HOLD is a reference only'}
- {'dimension': 'out_of_sample_degradation', 'classification': 'MIXED', 'justification': 'compare train and later validation explicitly'}

## Candidate assessment

`NOT_CANDIDATE` — no automatic production approval is performed.

## Limitations

Results are research-only. No authenticated endpoint or real order was used. Past results do
not guarantee future results.
