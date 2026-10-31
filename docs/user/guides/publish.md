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
| `terraform-provider-<type>_<version>_SHA256SUMS` | yes |
| `terraform-provider-<type>_<version>_SHA256SUMS.sig` | yes, a binary detached signature |
| `terraform-provider-<type>_<version>_manifest.json` | no; without it the protocol is 5.0 |

The request stores the archives and returns a task. The task fails if the signature
does not verify against the keyring, or if the uploaded archives and the archives
`SHA256SUMS` lists are not the same set with the same digests. Uploading a version that
is already published with different checksums answers 409; uploading the same release
again changes nothing.

OpenTofu then installs it as `<TOFU_REGISTRY_HOSTNAME>/acme/widget`.
