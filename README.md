# pulp-tofu

A [Pulp](https://pulpproject.org/) plugin that serves OpenTofu providers and modules.

> **Beta.** pulp_tofu is a third-party plugin, developed outside the Pulp Project. You
> install it yourself, its documentation lives on its own site, and its API may change
> between releases.

Documentation: <https://chad-loder.github.io/pulp_tofu/>

- **Pull-through mirror.** A distribution answers the
  [provider network mirror protocol](https://opentofu.org/docs/internals/provider-network-mirror-protocol/).
  On the first request for a provider, Pulp fetches it from the upstream registry and
  verifies its signed checksums. Pulp answers later requests itself, even while the
  upstream is down.
- **Registry.** With `TOFU_REGISTRY_ENABLED`, Pulp answers OpenTofu's provider and module
  registry protocols at its own hostname. `source = "pulp.example/acme/demo"` then needs
  no CLI configuration.
- **Publishing.** Namespaces accept provider releases under GoReleaser's file names, and
  module packages. Pulp checks each release against the namespace's keyring, or signs it
  with a Pulp signing service.
- **Private namespaces.** Reads follow Pulp's roles. Registry tokens with `read`,
  `update` or `publish` scope serve CI jobs and `TF_TOKEN_*` users.

```hcl
provider_installation {
  network_mirror {
    url = "https://pulp.example/pulp/tofu/tofu/"
  }
}
```

[pulp-cli-tofu](https://github.com/chad-loder/pulp-cli-tofu)
adds `pulp tofu` commands to pulp-cli.

| path | holds |
| --- | --- |
| `pulp_tofu/app/` | models, REST API, registry views, content-app mirror handler, upstream client, tasks |
| `pulp_tofu/tests/unit/` | tests that need no Pulp |
| `pulp_tofu/tests/functional/` | tests run inside a Pulp container: a fake registry, and `tofu init` against Pulp |
| `dev/` | Django settings for type checking outside a deployment |
| `docs/` | user, admin and developer documentation |

Development: [docs/dev/index.md](docs/dev/index.md).

## How to File an Issue

File through this project's GitHub issues, not Pulp's trackers.

> **WARNING** Is this security related? If so, report it privately through
> [GitHub's vulnerability reporting](https://github.com/chad-loder/pulp_tofu/security/advisories/new).
