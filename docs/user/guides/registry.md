# Serve Providers as a Registry

!!! warning "Tech preview"
    The registry is a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).
    Its API may change between releases.

With the registry on, OpenTofu resolves source addresses under Pulp's hostname with no
CLI configuration:

```hcl
terraform {
  required_providers {
    random = { source = "pulp.example/hashicorp/random" }
  }
}
```

An administrator turns the registry on and sets the hostname; see
[Set the Registry Hostname](site:pulp_tofu/docs/admin/guides/registry-hostname/).

## Create a namespace

A namespace is the middle part of a source address. Each one names the distribution
whose repository answers for it.

Prerequisites:

- The registry turned on; see
  [Set the Registry Hostname](site:pulp_tofu/docs/admin/guides/registry-hostname/).

To serve `pulp.example/hashicorp/...`:

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
| `distribution` | The tofu distribution whose latest repository version is served. |
| `content_hostname` | The origin hostname of the content to serve. Empty serves content whose origin is `TOFU_REGISTRY_HOSTNAME`. |
| `private` | `true` answers only users with `tofu.view_tofunamespace` on the namespace. |

`content_hostname` re-homes mirrored or synced content: with the namespace above,
`pulp.example/hashicorp/random` serves the `registry.opentofu.org/hashicorp/random`
releases in the repository. The upstream signature still verifies, because the download
document lists the key that signed the stored `SHA256SUMS`.

The registry serves only what the repository version holds. Sync the providers first,
or let the distribution's mirror pull them through; see
[Sync Providers](site:pulp_tofu/docs/user/guides/sync/).

## Registry endpoints

| request | served by |
| --- | --- |
| `/.well-known/terraform.json` | API workers |
| `/tofu/v1/providers/<namespace>/<type>/versions` | API workers |
| `/tofu/v1/providers/<namespace>/<type>/<version>/download/<os>/<arch>` | API workers |
| archive, `SHA256SUMS`, `SHA256SUMS.sig` | content app, below the distribution's mirror path |

A version is listed only if its checksums were signed: OpenTofu requires a signing key
from every registry except `registry.opentofu.org`.

## Grant read access to a private namespace

A private namespace refuses anonymous registry requests with 401 and users without the
view permission with 403. For tokens and archive protection, see
[Run a Private Registry](site:pulp_tofu/docs/user/guides/private/).

To let `alice` read a namespace:

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

This issue occurs when the namespace's distribution has no version of that provider in
its repository. The registry serves only what the repository holds; it does not pull
through. It also occurs when `content_hostname` does not match the registry the
provider came from.

To resolve it, sync the provider into the repository, or install it once through the
distribution's network mirror so that Pulp pulls it through. Then check that
`content_hostname` names the registry the provider came from, for example
`registry.opentofu.org`.
