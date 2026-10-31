# Beta Status

pulp_tofu is a beta plugin developed outside the Pulp Project. It builds on pulpcore's
plugin API and follows Pulp's conventions, but it is not a Pulp Project component:

- Pulp's container images and the Pulp operator do not include it; install it yourself,
  as in [Install the Plugin](site:pulp_tofu/docs/admin/guides/install/).
- Its documentation lives on its own site, not on pulpproject.org.
- Report problems to [its GitHub issues](https://github.com/chad-loder/pulp_tofu/issues),
  not to Pulp's trackers.
- Releases may change its API without a deprecation period.

## Tech preview features

Within the beta, these features are newer, and their API is the most likely to change:

- the provider registry: namespaces, the registry settings and the registry
  endpoints. See [Serve Providers as a Registry](site:pulp_tofu/docs/user/guides/registry/).
- publishing: `allow_uploads`, namespace keyrings and the namespace `publish` action.
  See [Publish Providers](site:pulp_tofu/docs/user/guides/publish/).
- registry tokens. See [Run a Private Registry](site:pulp_tofu/docs/user/guides/private/).
- Pulp signing: `TofuSigningService` and namespace `signing_service`. See
  [Sign Releases with Pulp](site:pulp_tofu/docs/admin/guides/signing/).
- modules: the `publish_module` action, module sync and the modules.v1 endpoints. See
  [Publish Modules](site:pulp_tofu/docs/user/guides/modules/).

## Changes not yet upstream

Managing pulp_tofu from OpenTofu needs a change to the third-party Pulp provider that is
not yet in its releases; see
[Manage Pulp with OpenTofu](site:pulp_tofu/docs/user/guides/opentofu-provider/).
