# Mirror Providers for OpenTofu

A mirror takes three objects. The remote names the upstream registry. The repository
keeps the providers Pulp has pulled. The distribution serves them to OpenTofu.

Each example has a **pulp-cli** tab, using the `pulp tofu` commands from
[pulp-cli-tofu](https://github.com/chad-loder/pulp-cli-tofu), and a **REST API** tab,
using `curl` with `$PULP` set to the API's origin, for example `https://pulp.example`.
The tab you choose stays chosen on every page.

## Create a remote

To create a remote for registry.opentofu.org:

=== "pulp-cli"

    ```bash
    pulp tofu remote create --name opentofu --url https://registry.opentofu.org/ \
      --policy on_demand
    ```

=== "REST API"

    ```bash
    curl -u admin --json '{
      "name": "opentofu",
      "url": "https://registry.opentofu.org/",
      "policy": "on_demand"
    }' "$PULP/pulp/api/v3/remotes/tofu/tofu/"
    ```

`url` is the registry root, where OpenTofu's service discovery starts. Each remote
serves one registry host. These fields are specific to this plugin:

| field (CLI option) | meaning |
| --- | --- |
| `policy` (`--policy`) | When Pulp stores archives. `on_demand` stores each archive the first time a client asks for it. `streamed` serves archives without storing them. `immediate` downloads archives during a [sync](site:pulp_tofu/docs/user/guides/sync/). |
| `includes`, `excludes` (`--include`, `--exclude`, repeatable) | `hostname/namespace/type` glob patterns, for example `registry.opentofu.org/hashicorp/*`. Pulp fetches no provider outside them. |
| `metadata_ttl` (`--metadata-ttl`) | How many seconds Pulp reuses a provider's version list before it refreshes the list. Default 3600. |
| `require_signatures` (`--require-signatures`) | Refuse unsigned providers from registry.opentofu.org. Other registries must always sign. |
| `signing_keys` (`--signing-key`, repeatable) | OpenPGP fingerprints this remote accepts. When empty, Pulp accepts the keys the registry publishes. |

The remote refuses `username` and `password`, because pulpcore would send them to the
archive download hosts as well as to the registry.

## Create a repository and a distribution

To serve the remote at the base path `tofu`:

=== "pulp-cli"

    ```bash
    pulp tofu repository create --name providers --retain-repo-versions 1
    pulp tofu distribution create --name tofu --base-path tofu \
      --remote opentofu --repository providers
    pulp tofu distribution show --name tofu
    ```

=== "REST API"

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

Every time Pulp pulls a provider archive, it adds the archive to a new repository
version, so orphan cleanup never deletes it. A busy mirror creates many versions, and
each one holds everything before it. With `retain_repo_versions` set to 1, Pulp keeps
only the latest version. Raise the number to keep versions you can roll back to.

The remote and the repository are both optional:

- With a remote and no repository, the distribution still serves, but the providers it
  pulls belong to no repository version.
- With a repository and no remote, the distribution serves only the repository's
  providers.

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

OpenTofu requires `https` for a network mirror. It sends no credentials when it
downloads an archive, so any content guard on the distribution must allow anonymous
reads.

## Lock files

For each archive, a version document lists a `zh:` hash taken from the signed
`SHA256SUMS`. After Pulp stores the archive, the document also lists its `h1:` hash.

`tofu init` records the hashes only for the platform it installs. To record every
platform you use, run `tofu providers lock` with those platforms, or set
`trust_all_hashes = true` in the `network_mirror` block. For details, see
[Provider Verification](site:pulp_tofu/docs/user/learn/verification/).
