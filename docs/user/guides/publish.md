# Publish Providers

!!! warning "Tech preview"
    Publishing is a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

A namespace accepts releases signed by a key in its keyring. Before Pulp serves a
release, it verifies the signature and every archive. Once published, a version never
changes.

## Set up a namespace

Keys live in a pulpcore OpenPGP keyring. pulp-cli has no keyring commands, so create
the keyring and add the publisher's public key through the REST API:

```bash
keyring=$(curl -u admin --json '{"name": "acme-keys"}' \
  "$PULP/pulp/api/v3/repositories/core/openpgp_keyring/" | jq -r .pulp_href)
curl -u admin -F file=@acme-signing-key.asc -F repository="$keyring" \
  "$PULP/pulp/api/v3/content/core/openpgp_publickey/"
```

Pulp adds each published release to the repository of a distribution that has
`allow_uploads` set. A distribution with a remote cannot accept uploads, so your own
releases never mix with pulled-through ones.

To create the namespace `acme`, checked against that keyring:

=== "pulp-cli"

    ```bash
    pulp tofu repository create --name acme
    pulp tofu distribution create --name acme --base-path acme --repository acme --allow-uploads
    pulp tofu namespace create --name acme --distribution acme --keyring "$keyring"
    ```

=== "REST API"

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

To publish, a user needs the `tofu.publish_tofunamespace` permission on the namespace.
The `tofu.tofunamespace_owner` and `tofu.tofunamespace_collaborator` roles grant it.

## Publish a release

Publish the files GoReleaser's provider template writes to `dist/`.

To publish version 1.2.0 of `widget` to `acme`:

=== "pulp-cli"

    ```bash
    pulp tofu namespace publish --name acme dist/terraform-provider-widget_1.2.0_*
    ```

=== "REST API"

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
| `terraform-provider-<type>_<version>_manifest.json` | no; without it, the protocol is 5.0 |

If the namespace has a signing service, you can leave out the signature, or both the
signature and `SHA256SUMS`, and Pulp signs the release. See
[Sign Releases with Pulp](site:pulp_tofu/docs/admin/guides/signing/).

pulp-cli uploads each file in chunks before it publishes, so large archives need no
extra steps. With the REST API, upload large archives first, in chunks, through
pulpcore's `uploads` or `artifacts` endpoints. Then name the artifacts in the release,
keyed by release filename:

```bash
curl -u ci --json '{"artifacts": {"terraform-provider-widget_1.2.0_linux_amd64.zip": "<artifact href>", ...}}' \
  "$PULP<namespace pulp_href>publish/"
```

`publish_module` also takes `artifact` in place of `file`.

Pulp stores the archives and returns a task. The task fails in two cases:

- The signature does not verify against the keyring.
- The uploaded archives do not match the archives `SHA256SUMS` lists, by name and
  digest.

If you publish a version again with different checksums, Pulp answers 409. If you
publish the same release again, nothing changes.

OpenTofu then installs the provider as `<TOFU_REGISTRY_HOSTNAME>/acme/widget`.

## Publish from GitHub Actions

After GoReleaser's provider release step, upload `dist/` with a registry token for the
namespace. The first release needs a token with `publish` scope. After that, `update`
scope is enough, and a leaked `update` token cannot publish new provider names. The job
hands the token to curl in a header file, so the token never appears in a process's
arguments:

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

This step uses curl because pulp-cli cannot send a bearer token. On pulpcore 3.120 or
later, the job can instead use the CI provider's OIDC token through pulpcore's Workload
Identity. See [Run a Private Registry](site:pulp_tofu/docs/user/guides/private/).

## Troubleshooting

When you publish providers, you might encounter the following issues.

### Error: `The namespace's distribution does not accept uploads.`

You might get a 400 response that states
`The namespace's distribution does not accept uploads.`

This issue occurs when the namespace's distribution has `allow_uploads` off. A PATCH
through the generated Python bindings can turn it off by accident. The bindings' update
model sends every field's default, so a PATCH that changes only `content_guard` also
sends `"allow_uploads": false`.

To resolve it, set `allow_uploads` to `true` again. Include it in every PATCH you make
through the bindings.

### Error: `provider binary not found`

You might get an error from `tofu init` that states:

```text
Error while installing pulp.example/acme/widget v1.2.0: provider binary not found:
could not find executable file starting with terraform-provider-widget
```

This issue occurs when an archive holds no executable named
`terraform-provider-<type>_v<version>`. Pulp checks the archives against the signed
`SHA256SUMS`, but not their contents. The release publishes, and fails only when
OpenTofu installs it.

To resolve it, build the archives with GoReleaser's provider template, which names the
binary and marks it executable. Then publish the release as a new version.
