# pulp-tofu

A [Pulp](https://pulpproject.org/) plugin that serves OpenTofu providers and modules.

- **Pull-through mirror.** A distribution answers the
  [provider network mirror protocol](https://opentofu.org/docs/internals/provider-network-mirror-protocol/).
  The first request for a provider fetches it from the upstream registry and verifies its
  signed checksums; later requests are answered from Pulp, also while the upstream is down.
- **Registry.** With `TOFU_REGISTRY_ENABLED`, Pulp answers OpenTofu's provider and module
  registry protocols at its own hostname, so `source = "pulp.example/acme/demo"` needs no
  CLI configuration.
- **Publishing.** Namespaces accept provider releases under GoReleaser's file names
  and module packages, checked against the namespace's keyring or signed by a Pulp signing service.
- **Private namespaces.** Reads follow Pulp's roles, and registry tokens scoped to
  `read`, `update` or `publish` serve CI and `TF_TOKEN_*` users.

```hcl
provider_installation {
  network_mirror {
    url = "https://pulp.example/pulp/tofu/tofu/"
  }
}
```

See the [documentation](docs/index.md). [pulp-cli-tofu](https://github.com/chad-loder/pulp-cli-tofu)
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

File through this project's GitHub issues.

> **WARNING** Is this security related? If so, follow the
> [Security Disclosures](https://pulpproject.org/help/security-disclosures/) procedure.
