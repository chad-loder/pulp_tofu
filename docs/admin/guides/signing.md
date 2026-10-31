# Sign Releases with Pulp

!!! warning "Tech preview"
    Pulp signing is a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

A namespace with a signing service accepts provider releases without a signature. Pulp
signs each release's `SHA256SUMS` with the service's key, so publishers need no key of
their own. The registry lists the service's public key, and OpenTofu verifies against
it.

## Register a service

A signing service is a script that Pulp's workers run with the path of a `SHA256SUMS`
file. The script must write a **binary** detached signature, because OpenTofu does not
read ASCII-armored ones. It must then print where it wrote the signature:

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

Registration signs test data, and refuses the service in three cases:

- The signature is armored.
- The signature does not verify against the key.
- The script signed with a different key.

## Sign a namespace's releases

To have Pulp sign the releases published to `acme`:

=== "pulp-cli"

    ```bash
    service=$(pulp signing-service show --name tofu-releases | jq -r .pulp_href)
    pulp tofu namespace update --name acme --signing-service "$service"
    ```

=== "REST API"

    ```bash
    service=$(curl -u admin "$PULP/pulp/api/v3/signing-services/?name=tofu-releases" \
      | jq -r '.results[0].pulp_href')
    curl -u admin -X PATCH --json '{"signing_service": "'"$service"'"}' "$PULP<namespace pulp_href>"
    ```

Publishers can then leave out `SHA256SUMS.sig`. They can leave out `SHA256SUMS` too, and
Pulp writes it from the archives, as GoReleaser would. If a release does carry its own
signature, that signature must still verify against the namespace's keyring.
