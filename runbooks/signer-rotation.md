# Remote signer credential rotation

Production uses distinct private signer boundaries for liveness,
operations/settlement, provider payout, finality oracle, sponsor/relayer,
withdrawal relayer, and blob publisher. Never rotate two roles onto the same
account, endpoint, credential or network policy.

## Prepare

Freeze only the affected mutation class. Record the current role, public
address, Web3Signer/OpenBao policy version, allowed method/path/CIDR, pending
nonces and durable operation IDs. Create the new key inside the signer/HSM;
never export raw private key material or place rotation tokens in evidence.

Grant the new identity only the reviewed role/method/path and validate an
unrelated account and method are denied. Keep the signer API internal. For
token rotation, configure the explicit previous-token field only for the
bounded owner-supported overlap; known development tokens are forbidden in
non-local profiles.

## Cut over and close

Drain or resolve ambiguous pending operations using their stored nonce and
exact signed bytes. Switch one deployment at a time, verify address/readiness,
then prove one allowed operation and one denied operation. Remove the old token
and network access, revoke/disable the old key, and verify both old credential
and unauthorized method now fail.

Abort on an address mismatch, nonce gap, widened CIDR, raw-key exposure,
unexpected allowed method, or ambiguous transaction. Seal only public
addresses, policy hashes/versions, image digests, allow/deny results, operation
hashes and timestamps; the sealing/rotation secrets remain outside evidence.
