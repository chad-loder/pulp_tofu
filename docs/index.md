# Welcome to Pulp OpenTofu

The `tofu` plugin extends [pulpcore](site:pulpcore/) to serve OpenTofu providers and
modules. A distribution answers OpenTofu's
[provider network mirror protocol](https://opentofu.org/docs/internals/provider-network-mirror-protocol/),
so `tofu init` installs providers from Pulp. The first request for a provider fetches it
from an upstream registry; later requests are answered from Pulp. Pulp can also serve
the provider and module registry protocols at its own hostname, for upstream content
and for releases published to it. This plugin is a part of the
[Pulp Project](site:/) and assumes some familiarity with the
[pulpcore documentation](site:pulpcore/).

To get started, [mirror providers for OpenTofu](site:pulp_tofu/docs/user/guides/mirror/).
[pulp-cli-tofu](https://github.com/chad-loder/pulp-cli-tofu) adds `pulp tofu` commands to
[pulp-cli](https://github.com/pulp/pulp-cli).

## Features

- Pull-through caching of providers from registry.opentofu.org or any provider registry
- Verification of every provider version's `SHA256SUMS` signature before it is served,
  optionally against fingerprints you pin
- Archives checked against the signed SHA-256 while they stream to the first client
- [Sync](site:pulp_tofu/docs/user/guides/sync/) to pre-fetch providers, for offline or
  air-gapped use
- `zh:` and `h1:` hashes in every version document, for OpenTofu's dependency lock file
- Interrupted upstream downloads resume instead of restarting
- Upstream metadata cached by HTTP rules, with conditional revalidation, remembered
  "not found" answers and retries that honor `Retry-After`
- Every change creates a restorable repository version
- A provider and module registry at Pulp's hostname (tech preview)
- Publishing of provider releases and module packages, verified against a namespace
  keyring or signed by a Pulp signing service (tech preview)
- Modules synced from a registry, from git or from archives, served as packages
  (tech preview)
- Private namespaces, Pulp roles at every level, and scoped registry tokens
  (tech preview)
- Domains and object storage (S3, Azure)

## How to use these docs

- [Mirror providers for OpenTofu](site:pulp_tofu/docs/user/guides/mirror/)
- [Sync providers ahead of use](site:pulp_tofu/docs/user/guides/sync/)
- [Serve providers as a registry](site:pulp_tofu/docs/user/guides/registry/)
- [Publish providers](site:pulp_tofu/docs/user/guides/publish/)
- [Publish and sync modules](site:pulp_tofu/docs/user/guides/modules/)
- [Run a private registry](site:pulp_tofu/docs/user/guides/private/)
- [How providers are verified](site:pulp_tofu/docs/user/learn/verification/)
- [Caching and upstream load](site:pulp_tofu/docs/user/learn/caching/)
- [Differences from registry.opentofu.org](site:pulp_tofu/docs/user/learn/differences/)
- [Tech preview features](site:pulp_tofu/docs/user/learn/tech-preview/)
- [Install the plugin](site:pulp_tofu/docs/admin/guides/install/)
- [Set the registry hostname](site:pulp_tofu/docs/admin/guides/registry-hostname/)
- [Sign releases with Pulp](site:pulp_tofu/docs/admin/guides/signing/)
- [Roles and permissions](site:pulp_tofu/docs/admin/guides/rbac/)
- [Settings](site:pulp_tofu/docs/admin/reference/settings/)
