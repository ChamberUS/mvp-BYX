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
- Corrupt manifests and invalid sessions still raise their existing errors.
- `scripts/continuous_capture.sh` is a versioned copy of the existing launcher
  with explicit retry classification: exit 0/75 -> 10 seconds; other errors ->
  300 seconds. `--retry-delay CODE` tests the policy without launching capture.

## Deployment and current process

The running supervisor (PID 62472) and recorder (PID 11337 at inspection), campaign
`ethusdt-futures-continuous-20261004T004905Z`, were not stopped, restarted, signalled
or replaced. Already imported Python functions keep the old code for that process.
The next CLI process imports the fixed code. The running supervisor still uses its
old retry policy: activation of the versioned launcher remains pending.

Do not rewrite a shell script while its interpreter is running. When capture is
already stopped in an authorized maintenance window, install the reviewed launcher
and resume through the existing mechanism; first check that neither the old
supervisor nor a recorder remains. Do not start a second instance just to test.
The versioned script is not installed automatically by this task.

## Verification

Targeted fixtures reproduce the exact empty-builder error, rejected first chunk,
bounded retry, later admitted session, unchanged completed resume, no duplication,
NO_SESSION/INCOMPLETE without false success, graceful stop during rejection,
corrupt state propagation, and supervisor 0/75/2 exit classification. All fixtures
are temporary and synthetic; production recordings and scientific cohorts are
not consumed. Ruff runs only on touched Python files. Default pytest coverage
expects the whole suite; targeted tests use `--no-cov` to avoid that unrelated
80% aggregate threshold.
