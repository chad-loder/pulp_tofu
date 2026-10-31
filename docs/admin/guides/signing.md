# Sign Releases with Pulp

!!! warning "Tech preview"
    Pulp signing is a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

A namespace with a signing service accepts provider releases without a signature, and
Pulp signs their `SHA256SUMS` with the service's key. Publishers then need no key of
their own; OpenTofu verifies against the service's public key, which the registry lists.

## Register a service

A signing service is a script Pulp's workers run with the path of a `SHA256SUMS` file.
It must write a **binary** detached signature, since OpenTofu does not read
ASCII-armored ones, and print where it wrote it:

```sh title="/var/lib/pulp/sign-tofu.sh"
#!/bin/sh
set -eu
gpg --batch --yes --local-user "$PULP_SIGNING_KEY_FINGERPRINT" \
  --detach-sign --output "$1.sig" "$1"
printf '{"file": "%s", "signature": "%s.sig"}\n' "$1" "$1"
```

Register it with pulpcore's command and pulp_tofu's class:

```bash
pulpcore-manager add-signing-service tofu-releases /var/lib/pulp/sign-tofu.sh <fingerprint> \
  --class tofu:TofuSigningService
```

Registration signs test data and refuses the service if the signature is armored, does
not verify against the key, or was made with another key.

## Use it

Set the namespace's `signing_service` to the service's `pulp_href` from
`/pulp/api/v3/signing-services/`. Releases then may leave out `SHA256SUMS.sig`, and may
leave out `SHA256SUMS` too, which Pulp writes from the archives as GoReleaser would. A
release that carries its own signature still has to verify against the namespace's
keyring.
