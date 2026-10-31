# Provider Verification

OpenTofu checks a provider installed from a network mirror only against the hashes the
mirror lists. It checks no signature itself. Pulp therefore does the checks OpenTofu
would make against the registry directly, before it serves a provider version:

1. The `SHA256SUMS` file must carry a detached OpenPGP signature that verifies against a
   signing key the registry publishes for the provider. With the remote's
   `signing_keys` set, it must verify against one of those fingerprints instead.
2. Providers from registry.opentofu.org may be unsigned, as OpenTofu allows by default.
   `require_signatures` refuses them. Providers from any other registry must be signed.
3. The registry's `shasum` for an archive must match its line in the signed
   `SHA256SUMS`, and the archive name must be a plain file name.

A version that fails any check is answered with `502` and is not stored.

Each archive downloads with the signed SHA-256 as its expected digest. If the bytes
differ, the download stops, the connection to the client is cut, and nothing is stored.
Under the `streamed` policy pulpcore checks nothing while streaming, and OpenTofu's own
hash check against the version document is the only check.

## Hashes in version documents

Each archive lists:

- `zh:` followed by the archive's SHA-256, taken from the signed `SHA256SUMS`;
- `h1:`, the hash of the archive's unpacked contents, once Pulp has stored the archive
  and computed it.

Pulp never publishes an `h1:` value it has not computed itself, because OpenTofu accepts
a package when any listed hash matches.

## Pinning signing keys

The registry delivers each provider's signing key in the same response that names the
archive, so a compromised registry could sign with a key of its own. Pinning closes that:

```bash
curl -u admin -X PATCH --json '{"signing_keys": ["E3E6E43D84CB852EADB0051D0C0AF313E5FD9F80"]}' \
  "$PULP<remote pulp_href>"
```

A `ProviderVersion`'s `signing_key_fingerprint` records which key verified it.
