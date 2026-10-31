# Provider Verification

When OpenTofu installs a provider from a network mirror, it checks the provider only
against the hashes the mirror lists. It checks no signature itself. So before Pulp serves
a provider version, Pulp makes the checks OpenTofu would make against the registry:

1. The `SHA256SUMS` file must carry a detached OpenPGP signature, and the signature must
   verify against a signing key the registry publishes for the provider. If the remote
   sets `signing_keys`, the signature must instead verify against one of those
   fingerprints.
2. Providers from registry.opentofu.org may be unsigned, as OpenTofu allows by default.
   A remote with `require_signatures` refuses them. Providers from any other registry
   must be signed.
3. The registry's `shasum` for an archive must match the archive's line in the signed
   `SHA256SUMS`. The archive name must be a plain file name.

If a version fails any check, Pulp answers `502` and stores nothing.

Pulp downloads each archive with the signed SHA-256 as its expected digest. If the bytes
differ, Pulp stops the download, cuts the connection to the client, and stores nothing.
Under the `streamed` policy, pulpcore checks nothing while it streams. OpenTofu's own
hash check against the version document is then the only check.

## Hashes in version documents

For each archive, a version document lists:

- `zh:` and the archive's SHA-256, taken from the signed `SHA256SUMS`.
- `h1:`, the hash of the archive's unpacked contents. Pulp lists it once it has stored
  the archive and computed the hash.

OpenTofu accepts a package when any listed hash matches. So Pulp lists only `h1:` values
it has computed itself.

## Pinning signing keys

The registry sends each provider's signing key in the same response that names the
archive. A compromised registry could therefore sign with a key of its own. Pinning
the keys you trust closes that gap.

To accept only HashiCorp's key on the remote `opentofu`:

=== "pulp-cli"

    ```bash
    pulp tofu remote update --name opentofu --signing-key E3E6E43D84CB852EADB0051D0C0AF313E5FD9F80
    ```

=== "REST API"

    ```bash
    curl -u admin -X PATCH --json '{"signing_keys": ["E3E6E43D84CB852EADB0051D0C0AF313E5FD9F80"]}' \
      "$PULP<remote pulp_href>"
    ```

Each `ProviderVersion` records the key that verified it, in `signing_key_fingerprint`.
