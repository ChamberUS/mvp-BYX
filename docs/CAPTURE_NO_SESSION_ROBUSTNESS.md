# Capture: NO_SESSION robustness

## Concrete incident cause

At 2026-10-04 00:08:03 UTC the first COMPLETE recording in campaign
`ethusdt-futures-continuous-20261003T233724Z` was rejected by scientific admission
(`PROVENANCE_INCOMPLETE|DIRTY_WORKTREE`). The CLI's rejected-admission branch
broke out of the capture loop before appending any session. Its unconditional
final call to `MicrostructureCampaignBuilder.build(..., ())` then raised
`ValueError: campaign requires at least one session`, producing exit code 2.
The existing supervisor mapped all nonzero exits to a 300-second sleep.
The previously observed gap was 5m39.498184s, including process/session overhead.

This is a lifecycle bug, not permission to admit dirty or invalid recordings.
`scientific_admission.py` requires clean provenance; those checks remain intact.
The builder still rejects an empty campaign with exactly the original exception.
No dataset selection, cohort, frozen specification or research gate changes.

## Small correction

- A rejected COMPLETE chunk keeps its rejection metadata and contributes no
  scientific duration or campaign session. The CLI retries capture in the same
  campaign with interruptible delays of 10, 20, 30, 40, 50, 60 seconds, capped at
  60. A valid admitted chunk resets this delay. No busy loop or fabricated session.
- If the run ends without an admitted session, the CLI emits `NO_SESSION`,
  `success=false`, exit 75. It never creates an empty campaign manifest.
- If existing admitted sessions exist but the target is not met, the result is
  `INCOMPLETE`, `success=false`, exit 75. Completed targets alone return exit 0.
- Valid campaign manifests are written only when a new admitted session is added;
  resuming an already complete campaign does not rewrite it or duplicate sessions.
  Existing raw session files are not edited by this fix.
- Corrupt campaign manifests and builder validation failures retain their existing
  errors. Rejected chunk reasons remain in scientific admission metadata.
- `scripts/continuous_capture.sh` is a versioned copy of the existing launcher
  with explicit retry classification: exit 0/75 -> 10 seconds; other errors ->
  300 seconds. `--retry-delay CODE` tests the policy without launching capture.

## Deployment and current process

Initial inspection found supervisor PID 62472 and recorder PID 11337, campaign
`ethusdt-futures-continuous-20261004T004905Z`, still on the old imported code.
At 2026-10-04 01:19:57 UTC that recorder autonomously exited with the same error
and exit 2. No recorder was interrupted by this task.

At 01:22:19.317668 UTC the supervisor was positively identified as idle in its
300-second backoff: its only children were `sleep 300` PID 13876 and caffeinate
PID 62516; no campaign recorder was active. The old supervisor received SIGTERM,
then its sleeping child received SIGTERM so the existing cleanup trap could run
promptly. Closure and absence of remaining children/PID registration were checked
before installing the reviewed versioned launcher. The previous launcher was
backed up outside Git as `~/.mvp-binance-capture/continuous_capture.pre-entitlements-v1.sh`.

The same launcher mechanism resumed with supervisor PID 13916, recorder PID 13923,
campaign `ethusdt-futures-continuous-20261004T012219Z`, session
`microstructure-20261004T012220Z-usd_m_futures`. Activation finished at
01:22:19.583628 UTC. The new CLI imports the fixed code and the installed launcher
uses the explicit 0/75/2 retry policy. New Binance depth request returned HTTP 200
at 01:22:21.873725 UTC, and the new event part-file was observed growing, proving
actual recording rather than only a live PID. No duplicate recorder was observed.

The previous session ended COMPLETE, last event 01:19:06.941 UTC, ended_at
01:19:09.670317 UTC. Recovery therefore included a gap after the autonomous
failure. The new first-event timestamp is unavailable until final metadata is
written; do not report an exact event-to-event gap from directory names. From
supervisor exit to resumed launch the interval was approximately 2m22.6s, below
the old 5-minute backoff. No thresholds, provenance rules or coortes were changed.
Private activation evidence remains outside Git in
`/private/tmp/byx-entitlements-phase/capture-activation.json`.

## Verification

Targeted fixtures reproduce the exact empty-builder error, rejected first chunk,
bounded retry, later admitted session, unchanged completed resume, no duplication,
NO_SESSION/INCOMPLETE without false success, graceful stop during rejection,
corrupt state propagation, and supervisor 0/75/2 exit classification. All fixtures
are temporary and synthetic; production recordings and scientific cohorts are
not consumed. Ruff runs only on touched Python files. Default pytest coverage
expects the whole suite; targeted tests use `--no-cov` to avoid that unrelated
80% aggregate threshold.
