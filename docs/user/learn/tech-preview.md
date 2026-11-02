# Beta Status

pulp_tofu is a beta plugin developed outside the Pulp Project. It builds on pulpcore's
plugin API and follows Pulp's conventions, but it is not a Pulp Project component:

- Pulp's container images and the Pulp operator do not include it. Install it yourself,
  as described in [Install the Plugin](site:pulp_tofu/docs/admin/guides/install/).
- Its documentation lives on its own site, not on pulpproject.org.
- Report problems to [its GitHub issues](https://github.com/chad-loder/pulp_tofu/issues),
  not to Pulp's trackers.
- Releases may change its API without a deprecation period.

## Changes not yet upstream

Managing pulp_tofu from OpenTofu needs a change to the third-party Pulp provider that is
not yet in its releases; see
[Manage Pulp with OpenTofu](site:pulp_tofu/docs/user/guides/opentofu-provider/).
