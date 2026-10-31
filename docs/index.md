# Welcome to Pulp OpenTofu

The `tofu` plugin extends [pulpcore](site:pulpcore/) to serve OpenTofu providers. A
distribution answers OpenTofu's
[provider network mirror protocol](https://opentofu.org/docs/internals/provider-network-mirror-protocol/),
so `tofu init` installs providers from Pulp. The first request for a provider fetches it
from an upstream registry; later requests are answered from Pulp. This plugin is a part
of the [Pulp Project](site:/) and assumes some familiarity with the
[pulpcore documentation](site:pulpcore/).

To get started, [mirror providers for OpenTofu](site:pulp_tofu/docs/user/guides/mirror/).

## Features

- Pull-through caching of providers from registry.opentofu.org or any provider registry
- Verification of every provider version's `SHA256SUMS` signature before it is served,
  optionally against fingerprints you pin
- Archives checked against the signed SHA-256 while they stream to the first client
- [Sync](site:pulp_tofu/docs/user/guides/sync/) to pre-fetch providers, for offline or
  air-gapped use
- `zh:` and `h1:` hashes in every version document, for OpenTofu's dependency lock file
- Interrupted upstream downloads resume instead of restarting
- Every change creates a restorable repository version

## How to use these docs

- [Mirror providers for OpenTofu](site:pulp_tofu/docs/user/guides/mirror/)
- [Sync providers ahead of use](site:pulp_tofu/docs/user/guides/sync/)
- [How providers are verified](site:pulp_tofu/docs/user/learn/verification/)
- [Caching and upstream load](site:pulp_tofu/docs/user/learn/caching/)
- [Install the plugin](site:pulp_tofu/docs/admin/guides/install/)
