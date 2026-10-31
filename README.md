# pulp-tofu

A [Pulp](https://pulpproject.org/) plugin that serves OpenTofu providers over the
[provider network mirror protocol](https://opentofu.org/docs/internals/provider-network-mirror-protocol/).
`tofu init` installs from Pulp; the first request for a provider fetches it from the
upstream registry, verifies its signed checksums, and later requests are answered from
Pulp.

```hcl
provider_installation {
  network_mirror {
    url = "https://pulp.example/pulp/tofu/tofu/"
  }
}
```

See the [documentation](docs/index.md):
[mirror providers](docs/user/guides/mirror.md), [sync ahead of use](docs/user/guides/sync.md),
[verification](docs/user/learn/verification.md), [caching](docs/user/learn/caching.md) and
[installation](docs/admin/guides/install.md).

| path | holds |
| --- | --- |
| `pulp_tofu/app/` | models, REST API, content-app mirror handler, upstream client, sync task |
| `pulp_tofu/tests/unit/` | tests that need no Pulp |
| `pulp_tofu/tests/functional/` | tests against a running Pulp and a fake registry |
| `dev/` | local settings, the end-to-end harness and bindings generation |
| `docs/` | user, admin and developer documentation |

Development: [docs/dev/index.md](docs/dev/index.md).

## How to File an Issue

File through this project's GitHub issues.

> **WARNING** Is this security related? If so, follow the
> [Security Disclosures](https://pulpproject.org/help/security-disclosures/) procedure.
