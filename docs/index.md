# Welcome to Pulp OpenTofu

!!! warning "Beta, and not part of the Pulp Project"
    pulp_tofu is developed outside the Pulp Project and is not in Pulp's images or on
    pulpproject.org. Its API may change between releases. See
    [Beta Status](site:pulp_tofu/docs/user/learn/tech-preview/).

With the `tofu` plugin, `tofu init` installs providers and modules from your Pulp
instead of the public registry. Installs keep working when the registry is slow or
down, and Pulp checks each provider against its publisher's signature before serving
it. You can also publish your own providers and modules to your Pulp.

It is a plugin for [Pulp](site:/) and assumes some familiarity with the
[pulpcore documentation](site:pulpcore/).

## What do you want to do?

| Goal | Read |
| --- | --- |
| Install providers through Pulp, so `tofu init` keeps working when the public registry is down | [Mirror Providers for OpenTofu](site:pulp_tofu/docs/user/guides/mirror/) |
| Install providers on machines with no internet access | [Sync Providers Ahead of Use](site:pulp_tofu/docs/user/guides/sync/) |
| Write `source = "pulp.example/acme/demo"`, with no CLI configuration on each machine | [Serve Providers as a Registry](site:pulp_tofu/docs/user/guides/registry/) |
| Release your own provider to Pulp from CI | [Publish Providers](site:pulp_tofu/docs/user/guides/publish/) |
| Release your own modules to Pulp | [Publish Modules](site:pulp_tofu/docs/user/guides/modules/) |
| Serve modules from the public registry or GitHub through Pulp | [Sync Providers Ahead of Use: Modules](site:pulp_tofu/docs/user/guides/sync/#modules) |
| Keep internal providers private and give CI a token | [Run a Private Registry](site:pulp_tofu/docs/user/guides/private/) |
| Declare Pulp remotes, repositories and distributions in OpenTofu code (needs an unreleased provider change) | [Manage Pulp with OpenTofu](site:pulp_tofu/docs/user/guides/opentofu-provider/) |

Prefer a command line to the REST API?
[pulp-cli-tofu](https://github.com/chad-loder/pulp-cli-tofu) adds `pulp tofu` commands
to [pulp-cli](https://github.com/pulp/pulp-cli).

## Set up Pulp

For administrators:

- [Install the Plugin](site:pulp_tofu/docs/admin/guides/install/): packages, webserver,
  TLS, domains and object storage.
- [Set the Registry Hostname](site:pulp_tofu/docs/admin/guides/registry-hostname/): turn on the
  registry and choose the name users write in `source` addresses.
- [Sign Releases with Pulp](site:pulp_tofu/docs/admin/guides/signing/): let Pulp sign
  releases that arrive unsigned.
- [Roles and Permissions](site:pulp_tofu/docs/admin/guides/rbac/): who can read,
  publish and administer.
- [Settings](site:pulp_tofu/docs/admin/reference/settings/): every setting the plugin
  adds.

## Understand how it works

- [Provider Verification](site:pulp_tofu/docs/user/learn/verification/): why you
  can trust what Pulp serves.
- [Caching and Upstream Load](site:pulp_tofu/docs/user/learn/caching/): what Pulp
  fetches, when, and what happens when the upstream fails.
- [Differences from registry.opentofu.org](site:pulp_tofu/docs/user/learn/differences/):
  what works the same and what does not.
- [Beta Status](site:pulp_tofu/docs/user/learn/tech-preview/): what being outside the
  Pulp Project means, which features may still change, and changes not yet upstream.
