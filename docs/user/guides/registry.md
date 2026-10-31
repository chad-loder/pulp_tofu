# Serve Providers as a Registry

!!! warning "Tech preview"
    The registry is a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).
    Its API may change between releases.

With the registry on, OpenTofu resolves source addresses under Pulp's hostname, and
users need no CLI configuration:

```hcl
terraform {
  required_providers {
    random = { source = "pulp.example/hashicorp/random" }
  }
}
```

An administrator turns the registry on and sets its hostname. See
[Set the Registry Hostname](site:pulp_tofu/docs/admin/guides/registry-hostname/).

## Create a namespace

A namespace is the middle part of a source address. Each namespace names one
distribution, and the registry answers for the namespace from that distribution's
repository.

Prerequisites:

- The registry turned on. See
  [Set the Registry Hostname](site:pulp_tofu/docs/admin/guides/registry-hostname/).

To serve `pulp.example/hashicorp/...` from the distribution `tofu`:

=== "pulp-cli"

    ```bash
    pulp tofu namespace create --name hashicorp --distribution tofu \
      --content-hostname registry.opentofu.org
    ```

=== "REST API"

    ```bash
    curl -u admin --json '{
      "name": "hashicorp",
      "distribution": "<distribution pulp_href>",
      "content_hostname": "registry.opentofu.org"
    }' "$PULP/pulp/api/v3/pulp_tofu/namespaces/"
    ```

| field | meaning |
| --- | --- |
| `name` | The namespace in source addresses. Unique per domain. |
| `distribution` | The tofu distribution to serve. The registry reads its latest repository version. |
| `content_hostname` | The registry the content came from. When empty, the namespace serves content published to this Pulp, whose origin is `TOFU_REGISTRY_HOSTNAME`. |
| `private` | When `true`, the namespace answers only users with `tofu.view_tofunamespace` on it. |

`content_hostname` serves mirrored or synced content under Pulp's own hostname. With
the namespace above, `pulp.example/hashicorp/random` serves the repository's releases of
`registry.opentofu.org/hashicorp/random`. The upstream signature still verifies,
because the download document lists the key that signed the stored `SHA256SUMS`.

The registry serves only the providers in the repository version. Sync them first, or
let the distribution's mirror pull them through. See
[Sync Providers Ahead of Use](site:pulp_tofu/docs/user/guides/sync/).

## Registry endpoints

| request | served by |
| --- | --- |
| `/.well-known/terraform.json` | API workers |
| `/tofu/v1/providers/<namespace>/<type>/versions` | API workers |
| `/tofu/v1/providers/<namespace>/<type>/<version>/download/<os>/<arch>` | API workers |
| archive, `SHA256SUMS`, `SHA256SUMS.sig` | content app, below the distribution's mirror path |

The registry lists a version only if someone signed its checksums. OpenTofu requires a
signing key from every registry except `registry.opentofu.org`.

## Grant read access to a private namespace

A private namespace answers anonymous registry requests with 401. It answers users
without the view permission with 403. For tokens and protecting archive downloads, see
[Run a Private Registry](site:pulp_tofu/docs/user/guides/private/).

To let `alice` read the namespace `acme`:

=== "pulp-cli"

    ```bash
    pulp tofu namespace role add --name acme --role tofu.tofunamespace_consumer --user alice
    ```

=== "REST API"

    ```bash
    curl -u admin --json '{"role": "tofu.tofunamespace_consumer", "users": ["alice"]}' \
      "$PULP<namespace pulp_href>add_role/"
    ```

## Troubleshooting

When OpenTofu installs from Pulp as a registry, you might encounter the following issues.

### Error: `provider registry ... does not have a provider named ...`

You might get an error that states:

```text
Could not retrieve the list of available versions for provider
pulp.example/hashicorp/random: provider registry pulp.example does not have a
provider named pulp.example/hashicorp/random
```

This issue occurs in two cases:

- The namespace's repository has no version of that provider. The registry serves only
  the repository's providers, and it does not pull providers through.
- The namespace's `content_hostname` does not match the registry the provider came
  from.

To resolve it, sync the provider into the repository, or install it once through the
distribution's network mirror so that Pulp pulls it through. Then check that
`content_hostname` names the registry the provider came from, for example
`registry.opentofu.org`.
