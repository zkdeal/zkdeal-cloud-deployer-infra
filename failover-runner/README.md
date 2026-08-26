# First-party failover assertion runner

`/opt/zkdeal-failover` executes the deployment promotion controller against a
real `failover-provider-v1` adapter and owner promotion route, then runs the
source-bound acceptance runner's split-brain scenario against the resulting
platform state. It joins both write-once records and refuses a fixture or
health-only success claim.

The controller still requires two independent health witnesses, scoped
provider/approval/owner credentials, signerless standby readiness, a provider-
captured durable primary target LSN, replay at or after that target, canonical
indexer freshness, and post-fence route/signer commit. Acceptance credentials
are mounted through role-specific files; no secret is copied into the result.

The release command is the exact flag set enforced by the Kurtosis package:

```text
/opt/zkdeal-failover --terminate-active --persist-primary-target-lsn \
  --assert-standby-replay --promote --assert-stale-writer-denied \
  --assert-rto-seconds 300
```

`PROMOTION_CONTROLLER_STATE_PATH` and `ACCEPTANCE_EVIDENCE_DIR` must be inside
the durable `FAILOVER_EVIDENCE_DIR`. Existing outputs fail closed; resume uses
a new candidate/fault attempt rather than overwriting evidence. Fixture-live
tests prove verifier mechanics only and cannot use the
`real-provider-owner-platform` release classification.
