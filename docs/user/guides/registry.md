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
[Registry Hostname](site:pulp_tofu/docs/admin/guides/registry-hostname/).

## Namespaces

A namespace is the middle part of a source address. Each one names the distribution
whose repository answers for it:

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

## What OpenTofu fetches

| request | served by |
| --- | --- |
| `/.well-known/terraform.json` | API workers |
| `/tofu/v1/providers/<namespace>/<type>/versions` | API workers |
| `/tofu/v1/providers/<namespace>/<type>/<version>/download/<os>/<arch>` | API workers |
| archive, `SHA256SUMS`, `SHA256SUMS.sig` | content app, below the distribution's mirror path |

A version is listed only if its checksums were signed: OpenTofu requires a signing key
from every registry except `registry.opentofu.org`.

## Private namespaces

OpenTofu sends `TF_TOKEN_<hostname>` as a bearer token on registry API requests, and no
credentials when downloading archives. A private namespace refuses anonymous registry
requests with 401 and users without the view permission with 403. The archive URLs stay
readable to anyone who knows them, as with any distribution without a content guard.
Grant read access with the namespace roles:

```bash
curl -u admin --json '{"role": "tofu.tofunamespace_consumer", "users": ["alice"]}' \
  "$PULP<namespace pulp_href>add_role/"
```
