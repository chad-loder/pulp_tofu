# Sync Providers Ahead of Use

A sync fetches the providers you choose into a new repository version, before any
client asks for them. Registries publish no list of all their providers, so each sync
names the providers it wants.

To fetch the two newest releases of `hashicorp/aws` and version 3.7.2 of
`hashicorp/random`, for two platforms:

=== "pulp-cli"

    ```bash
    pulp tofu repository sync --name providers --remote opentofu \
      --provider hashicorp/aws --latest 2 --provider hashicorp/random@3.7.2 \
      --platform linux_amd64 --platform darwin_arm64
    ```

=== "REST API"

    ```bash
    curl -u admin --json '{
      "remote": "<remote pulp_href>",
      "providers": [
        {"namespace": "hashicorp", "type": "aws", "latest": 2},
        {"namespace": "hashicorp", "type": "random", "versions": ["3.7.2"]}
      ],
      "platforms": ["linux_amd64", "darwin_arm64"]
    }' "$PULP/pulp/api/v3/repositories/tofu/tofu/<repository id>/sync/"
    ```

| field (CLI option) | meaning |
| --- | --- |
| `providers[].versions` (`--provider ns/type@v1,v2`) | Exact versions to fetch. When empty, the sync takes the newest releases. |
| `providers[].latest` (`--latest`) | How many of the newest releases to fetch when you name no versions. Default 1. The sync skips pre-releases. |
| `platforms` (`--platform`) | The `os_arch` platforms to fetch archives for. When empty, the sync fetches all. |
| `mirror` (`--mirror`) | When `true`, the sync removes every provider it did not select from the repository. |

The sync verifies the `SHA256SUMS` signature of every version it selects. The remote's
policy decides when the archives download:

- `immediate`: during the sync, and Pulp records their `h1:` hashes.
- `on_demand`: the first time a client asks for each one.

## Serve providers offline

Sync with an `immediate` remote. Then serve the repository through a distribution that
has no remote:

=== "pulp-cli"

    ```bash
    pulp tofu distribution create --name tofu-offline --base-path tofu-offline \
      --repository providers
    ```

=== "REST API"

    ```bash
    curl -u admin --json '{
      "name": "tofu-offline",
      "base_path": "tofu-offline",
      "repository": "<repository pulp_href>"
    }' "$PULP/pulp/api/v3/distributions/tofu/tofu/"
    ```

This distribution serves only the repository's providers and never contacts the
registry.

## Sync modules

!!! warning "Tech preview"
    Module sync is a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

A sync can also fetch modules. Registries do not host module packages themselves. A
registry's download answer points at the module's source, usually a git repository at
a commit. Pulp checks the source out, packs it, and serves the package itself.

To fetch the two newest versions of `cloudposse/label/null`:

=== "pulp-cli"

    ```bash
    pulp tofu repository sync --name modules --remote opentofu \
      --module cloudposse/label/null --latest 2
    ```

=== "REST API"

    ```bash
    curl -u admin --json '{
      "remote": "<remote pulp_href>",
      "modules": [{"namespace": "cloudposse", "name": "label", "system": "null", "latest": 2}]
    }' "$PULP/pulp/api/v3/repositories/tofu/tofu/<repository id>/sync/"
    ```

To serve the modules under Pulp's hostname, create a namespace and set its
`content_hostname` to the registry's hostname, as for synced providers. OpenTofu then
installs `pulp.example/cloudposse/label/null`.

| source | supported |
| --- | --- |
| `git::https://...` on any host: GitHub, GitLab, Forgejo, Gitea | yes, with `//subdir` and `?ref=` |
| `github.com/...` and `gitlab.com/...` shorthand | yes |
| HTTPS `.tar.gz`, `.tgz` or `.zip` archives, or `?archive=` | yes |
| git over SSH, `s3::`, `gcs::`, `hg::` | no |
| repositories or archives that need credentials | no |

Git uses the remote's `ca_cert` and proxy settings. Packing is deterministic: the same
commit always produces the same package digest, so Pulp never fetches a version it
already holds. Only a sync fetches modules. Pulp does not pull modules through on
demand.
