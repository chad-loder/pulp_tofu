# Mirror Providers for OpenTofu

A remote names the upstream registry, a repository records what Pulp has pulled, and a
distribution serves both to OpenTofu. The examples use `curl` against the REST API, with
`$PULP` set to the API root's origin, for example `https://pulp.example`.

## Create a remote

```bash
curl -u admin --json '{
  "name": "opentofu",
  "url": "https://registry.opentofu.org/",
  "policy": "on_demand"
}' "$PULP/pulp/api/v3/remotes/tofu/tofu/"
```

`url` is the registry root, where OpenTofu's service discovery starts. One remote serves
one registry host. The fields specific to this plugin:

| field | meaning |
| --- | --- |
| `policy` | `on_demand` stores each archive the first time a client asks for it; `streamed` serves archives without storing them; `immediate` downloads archives during [sync](sync.md). |
| `includes`, `excludes` | `hostname/namespace/type` glob patterns, for example `registry.opentofu.org/hashicorp/*`. Providers outside them are not fetched. |
| `metadata_ttl` | Seconds a provider's version list is reused before it is refreshed. Default 3600. |
| `require_signatures` | Refuse unsigned providers from registry.opentofu.org. Other registries must always sign. |
| `signing_keys` | OpenPGP fingerprints this remote accepts. Empty accepts the keys the registry publishes. |

The remote rejects `username` and `password`: pulpcore would send them to archive
download hosts as well as the registry.

## Create a repository and a distribution

```bash
curl -u admin --json '{"name": "providers", "retain_repo_versions": 1}' \
  "$PULP/pulp/api/v3/repositories/tofu/tofu/"

curl -u admin --json '{
  "name": "tofu",
  "base_path": "tofu",
  "remote": "<remote pulp_href>",
  "repository": "<repository pulp_href>"
}' "$PULP/pulp/api/v3/distributions/tofu/tofu/"
```

Each provider archive Pulp pulls is added to a new version of the repository, which
keeps it from orphan cleanup. A busy mirror makes many versions, each holding
everything before it, so `retain_repo_versions` of 1 keeps only the latest; raise it to
keep versions you can roll back to. A distribution with a remote and no repository still
serves, but what it pulls belongs to no repository version. A distribution with a
repository and no remote serves only what the repository holds.

The distribution's `base_url` is the mirror URL, for example
`https://pulp.example/pulp/tofu/tofu/`.

## Point OpenTofu at the mirror

In the [CLI configuration file](https://opentofu.org/docs/cli/config/config-file/):

```hcl
provider_installation {
  network_mirror {
    url     = "https://pulp.example/pulp/tofu/tofu/"
    include = ["registry.opentofu.org/*/*"]
  }
  direct {
    exclude = ["registry.opentofu.org/*/*"]
  }
}
```

OpenTofu requires `https` for a network mirror. It sends no credentials when it fetches
an archive, so a content guard on the distribution must admit anonymous reads.

## Lock files

A version document lists each archive's `zh:` hash, from the signed `SHA256SUMS`, and
its `h1:` hash once Pulp has stored the archive. `tofu init` records the hashes for the
platform it installs. To record every platform, run `tofu providers lock` with the
platforms you use, or set `trust_all_hashes = true` in the `network_mirror` block; see
[How providers are verified](site:pulp_tofu/docs/user/learn/verification/).
