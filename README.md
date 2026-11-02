# pulp-tofu

A [Pulp](https://pulpproject.org/) plugin that serves OpenTofu providers and modules.

> **Beta.** pulp_tofu is a third-party plugin, developed outside the Pulp Project. You
> install it yourself, its documentation lives on its own site, and its API may change
> between releases.

Documentation: <https://chad-loder.github.io/pulp_tofu/>

- **Pull-through mirror.** OpenTofu installs public providers through Pulp. Pulp fetches
  each provider the first time someone asks for it, checks its publisher's signature,
  and keeps a copy for later installs.
- **Sync.** Fetch chosen providers and modules ahead of time, for machines with no
  internet access.
- **Registry.** Users write `source = "pulp.example/acme/demo"`, and `tofu init` installs
  from Pulp with no CLI configuration on each machine.
- **Publishing.** Release your own providers and modules to Pulp from CI. Pulp checks
  each provider release against keys you register, or signs it for you.
- **Private namespaces.** Only users you allow can install from a private namespace. CI
  jobs get tokens limited to reading, updating or publishing.

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
