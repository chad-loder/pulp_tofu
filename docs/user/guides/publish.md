# Publish Providers

!!! warning "Tech preview"
    Publishing is a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

A namespace accepts releases signed by a key in its keyring. Pulp verifies the
signature and every archive before the release is served, and a published version
never changes.

## Set up a namespace

Keys live in a pulpcore OpenPGP keyring:

```bash
keyring=$(curl -u admin --json '{"name": "acme-keys"}' \
  "$PULP/pulp/api/v3/repositories/core/openpgp_keyring/" | jq -r .pulp_href)
curl -u admin -F file=@acme-signing-key.asc -F repository="$keyring" \
  "$PULP/pulp/api/v3/content/core/openpgp_publickey/"
```

Published releases go into the repository of a distribution with `allow_uploads`. A
distribution with a remote cannot accept uploads, so in-house releases never mix with
pull-through content:

```bash
curl -u admin --json '{"name": "acme"}' "$PULP/pulp/api/v3/repositories/tofu/tofu/"
curl -u admin --json '{
  "name": "acme", "base_path": "acme",
  "repository": "<repository pulp_href>", "allow_uploads": true
}' "$PULP/pulp/api/v3/distributions/tofu/tofu/"
curl -u admin --json '{
  "name": "acme", "distribution": "<distribution pulp_href>", "keyring": "'"$keyring"'"
}' "$PULP/pulp/api/v3/pulp_tofu/namespaces/"
```

Publishing needs the `tofu.publish_tofunamespace` permission on the namespace, which the
`tofu.tofunamespace_owner` and `tofu.tofunamespace_collaborator` roles grant.

## Publish a release

Upload a release as GoReleaser's provider template writes it to `dist/`:

```bash
cd dist
curl -u ci $(for f in terraform-provider-widget_1.2.0_*; do printf -- '-F files=@%s ' "$f"; done) \
  "$PULP<namespace pulp_href>publish/"
```

| file | required |
| --- | --- |
| `terraform-provider-<type>_<version>_<os>_<arch>.zip` | one or more |
| `terraform-provider-<type>_<version>_SHA256SUMS` | yes, unless Pulp signs |
| `terraform-provider-<type>_<version>_SHA256SUMS.sig` | yes, a binary detached signature, unless Pulp signs |

When the namespace has a signing service, a release may come without the signature, or
without the signature and `SHA256SUMS`, and Pulp signs it; see
[Sign Releases with Pulp](site:pulp_tofu/docs/admin/guides/signing/).
| `terraform-provider-<type>_<version>_manifest.json` | no; without it the protocol is 5.0 |

The request stores the archives and returns a task. The task fails if the signature
does not verify against the keyring, or if the uploaded archives and the archives
`SHA256SUMS` lists are not the same set with the same digests. Uploading a version that
is already published with different checksums answers 409; uploading the same release
again changes nothing.

OpenTofu then installs it as `<TOFU_REGISTRY_HOSTNAME>/acme/widget`.

## Publish from GitHub Actions

After GoReleaser's provider release step, upload `dist/` with a registry token that has
`publish` scope on the namespace. The token goes to curl in a header file, so it never
appears in a process's arguments:

```yaml
- name: Publish to Pulp
  env:
    PULP_TOKEN: ${{ secrets.PULP_TOFU_TOKEN }}
    NAMESPACE: /pulp/api/v3/pulp_tofu/namespaces/0190f7c4-.../
  run: |
    printf 'Authorization: Bearer %s\n' "$PULP_TOKEN" > "$RUNNER_TEMP/pulp-auth"
    cd dist
    files=()
    for f in terraform-provider-widget_"${GITHUB_REF_NAME#v}"_*; do files+=(-F "files=@$f"); done
    curl -fsS -H @"$RUNNER_TEMP/pulp-auth" "${files[@]}" "https://pulp.example${NAMESPACE}publish/"
```

On pulpcore 3.120 or later, a job can use the provider's OIDC token through pulpcore's
Workload Identity instead of a stored token; see
[Run a Private Registry](site:pulp_tofu/docs/user/guides/private/).
