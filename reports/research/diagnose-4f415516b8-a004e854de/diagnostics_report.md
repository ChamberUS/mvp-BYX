# Diagnostics report

## Period and protection

- Experiment: `diagnose-4f415516b8-a004e854de`
- Dataset: `BINANCE-ETHUSDT-1h-4f415516b84c4291`
- Dataset hash: `4f415516b84c4291e3922c901c1ffd3d1cb5a819f249c421805f154318aa46bc`
- Consumed test data is not used for selection, ranking, sensitivity, or interval choice.
- This report is diagnostic and post-event; it is not a production approval.

## Decision funnel

- all_segments: {'scope': 'all_segments', 'total_candles_evaluated': 34961, 'eligible_after_warmup': 34961, 'trending_up': 443, 'ema_confirmed': 17565, 'volume_confirmed': 12509, 'volatility_acceptable': 34925, 'risk_reward_acceptable': 158, 'buy_signals': 158, 'risk_approved': 33, 'orders_executed': 33, 'closed_trades': 33, 'eligible_after_warmup_percent': Decimal('100'), 'trending_up_percent': Decimal('1.267126226366522696719201396'), 'ema_confirmed_percent': Decimal('50.24169789193672949858413661'), 'volume_confirmed_percent': Decimal('35.77986899688224021052029404'), 'volatility_acceptable_percent': Decimal('99.89702811704470695918308973'), 'risk_reward_acceptable_percent': Decimal('0.4519321529704527902519950802'), 'buy_signals_percent': Decimal('0.4519321529704527902519950802'), 'risk_approved_percent': Decimal('0.09439089270901862074883441549'), 'orders_executed_percent': Decimal('0.09439089270901862074883441549')}
- development: {'scope': 'development', 'total_candles_evaluated': 27949, 'eligible_after_warmup': 27949, 'trending_up': 284, 'ema_confirmed': 13870, 'volume_confirmed': 9975, 'volatility_acceptable': 27913, 'risk_reward_acceptable': 93, 'buy_signals': 93, 'risk_approved': 21, 'orders_executed': 21, 'closed_trades': 21, 'eligible_after_warmup_percent': Decimal('100'), 'trending_up_percent': Decimal('1.016136534401946402375755841'), 'ema_confirmed_percent': Decimal('49.62610469068660774983004759'), 'volume_confirmed_percent': Decimal('35.69000679809653297076818491'), 'volatility_acceptable_percent': Decimal('99.87119396042792228702279151'), 'risk_reward_acceptable_percent': Decimal('0.3327489355612007585244552578'), 'buy_signals_percent': Decimal('0.3327489355612007585244552578'), 'risk_approved_percent': Decimal('0.07513685641704533257003828402'), 'orders_executed_percent': Decimal('0.07513685641704533257003828402')}
- validation: {'scope': 'validation', 'total_candles_evaluated': 7012, 'eligible_after_warmup': 7012, 'trending_up': 159, 'ema_confirmed': 3695, 'volume_confirmed': 2534, 'volatility_acceptable': 7012, 'risk_reward_acceptable': 65, 'buy_signals': 65, 'risk_approved': 12, 'orders_executed': 12, 'closed_trades': 12, 'eligible_after_warmup_percent': Decimal('100'), 'trending_up_percent': Decimal('2.267541357672561323445521962'), 'ema_confirmed_percent': Decimal('52.69537934968625213918996007'), 'volume_confirmed_percent': Decimal('36.13804905875641756988020536'), 'volatility_acceptable_percent': Decimal('100'), 'risk_reward_acceptable_percent': Decimal('0.9269823160296634341129492299'), 'buy_signals_percent': Decimal('0.9269823160296634341129492299'), 'risk_approved_percent': Decimal('0.1711351968054763262977752424'), 'orders_executed_percent': Decimal('0.1711351968054763262977752424')}

## HOLD reasons

Future returns in `hold_reason_analysis.csv` are calculated offline after traces were recorded;
they are never provided to the strategy.

- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 732, 'percent': Decimal('2.103266959744849581932592018'), 'horizon_candles': 1, 'future_return_mean': Decimal('0.0001837744826321144193402123824'), 'maximum_favorable_movement_mean': Decimal('0.007056033500367278015916707557'), 'maximum_adverse_movement_mean': Decimal('-0.007035723530295537749621052383'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 732, 'percent': Decimal('2.103266959744849581932592018'), 'horizon_candles': 3, 'future_return_mean': Decimal('0.0007804809876212696969482253512'), 'maximum_favorable_movement_mean': Decimal('0.01294378346998637500821241103'), 'maximum_adverse_movement_mean': Decimal('-0.01215550517542675279981352023'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 732, 'percent': Decimal('2.103266959744849581932592018'), 'horizon_candles': 6, 'future_return_mean': Decimal('0.001498126851195455955616837918'), 'maximum_favorable_movement_mean': Decimal('0.01843272283820782404041301663'), 'maximum_adverse_movement_mean': Decimal('-0.01673605461631831426851237488'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 732, 'percent': Decimal('2.103266959744849581932592018'), 'horizon_candles': 12, 'future_return_mean': Decimal('0.003187356290769641951403380582'), 'maximum_favorable_movement_mean': Decimal('0.02706623501807378141153852940'), 'maximum_adverse_movement_mean': Decimal('-0.02257836900070132672695426993'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'POSITION_ALREADY_OPEN', 'count': 732, 'percent': Decimal('2.103266959744849581932592018'), 'horizon_candles': 24, 'future_return_mean': Decimal('0.005577583331149604217557105296'), 'maximum_favorable_movement_mean': Decimal('0.03947106219232019077739583915'), 'maximum_adverse_movement_mean': Decimal('-0.03146429038519903401750642443'), 'regimes': 'RANGING,TRENDING_UP', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 33981, 'percent': Decimal('97.63813464356520989569864667'), 'horizon_candles': 1, 'future_return_mean': Decimal('0.00001397199408993409552595357375'), 'maximum_favorable_movement_mean': Decimal('0.004703545864231171628834591295'), 'maximum_adverse_movement_mean': Decimal('-0.004910705069323995350332570148'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 33981, 'percent': Decimal('97.63813464356520989569864667'), 'horizon_candles': 3, 'future_return_mean': Decimal('0.00003703779598969734752028674074'), 'maximum_favorable_movement_mean': Decimal('0.008329789352854339886913095650'), 'maximum_adverse_movement_mean': Decimal('-0.008830688447303721746350752344'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 33981, 'percent': Decimal('97.63813464356520989569864667'), 'horizon_candles': 6, 'future_return_mean': Decimal('0.00007873732009389051654908525734'), 'maximum_favorable_movement_mean': Decimal('0.01204043267817579769164202328'), 'maximum_adverse_movement_mean': Decimal('-0.01282944848296915489698854305'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 33981, 'percent': Decimal('97.63813464356520989569864667'), 'horizon_candles': 12, 'future_return_mean': Decimal('0.0001436701400386839981783668448'), 'maximum_favorable_movement_mean': Decimal('0.01747404107754489797228592611'), 'maximum_adverse_movement_mean': Decimal('-0.01859252503395934346704728901'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}
- {'reason_code': 'REGIME_NOT_UP', 'count': 33981, 'percent': Decimal('97.63813464356520989569864667'), 'horizon_candles': 24, 'future_return_mean': Decimal('0.0003729933495050289782274440887'), 'maximum_favorable_movement_mean': Decimal('0.02534730642019341159734666792'), 'maximum_adverse_movement_mean': Decimal('-0.02680259257320576095537787953'), 'regimes': 'RANGING,TRENDING_DOWN', 'post_event_only': True}

## Entries

- Entry diagnostic rows: 33

## Exits

- {'exit_reason': 'STOP_LOSS', 'count': 23, 'average_net_pnl': Decimal('-15.69017908905724511525217391'), 'median_net_pnl': Decimal('-16.450557657197625287'), 'win_rate': Decimal('0'), 'mfe_mean': Decimal('49.14516008695652173913043478'), 'mae_mean': Decimal('-79.73049208695652173913043478'), 'costs': Decimal('53.21740752579299895780000000'), 'holding_mean': Decimal('23'), 'pnl_contribution_percent_of_absolute_pnl': Decimal('-53.93977349061692358399245117'), 'result_without_exit_type': Decimal('308.1574613514274993670')}
- {'exit_reason': 'TAKE_PROFIT', 'count': 10, 'average_net_pnl': Decimal('30.8157461351427499367'), 'median_net_pnl': Decimal('31.1966157619126461537'), 'win_rate': Decimal('100'), 'mfe_mean': Decimal('185.01921710'), 'mae_mean': Decimal('-33.97978290'), 'costs': Decimal('24.35027869926689713300000000'), 'holding_mean': Decimal('31.7'), 'pnl_contribution_percent_of_absolute_pnl': Decimal('46.06022650938307641600754883'), 'result_without_exit_type': Decimal('-360.8741190483166376508')}

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

- {'dimension': 'net_return', 'classification': 'POOR', 'justification': 'net return across completed segments'}
- {'dimension': 'drawdown', 'classification': 'GOOD', 'justification': 'worst drawdown=1.270470357127358153094159219%'}
- {'dimension': 'trade_sample', 'classification': 'GOOD', 'justification': 'closed trades=33'}
- {'dimension': 'fold_consistency', 'classification': 'POOR', 'justification': 'positive folds=0/2'}
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
