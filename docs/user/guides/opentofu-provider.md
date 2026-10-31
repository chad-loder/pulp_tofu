# Manage Pulp with OpenTofu

!!! warning "Needs an unreleased provider change"
    The Pulp provider for OpenTofu and Terraform,
    [e-breuninger/pulp](https://github.com/e-breuninger/terraform-provider-pulp), does
    not yet accept the `tofu` plugin. Until a change adding it is merged and released,
    its `pulp_remote`, `pulp_repository` and `pulp_distribution` resources refuse
    `content_type = "tofu"`. Build the provider with that change, and point OpenTofu at
    the build with a `dev_overrides` block.

The `e-breuninger/pulp` provider declares Pulp remotes, repositories and distributions as
OpenTofu resources. With `tofu` added to its accepted plugin names, it manages a
pull-through mirror:

```hcl
terraform {
  required_providers {
    pulp = { source = "e-breuninger/pulp" }
  }
}

resource "pulp_remote" "opentofu" {
  content_type = "tofu"
  plugin_name  = "tofu"
  name         = "opentofu"
  url          = "https://registry.opentofu.org/"
  policy       = "streamed"
}

resource "pulp_repository" "providers" {
  content_type = "tofu"
  plugin_name  = "tofu"
  name         = "providers"
  description  = "providers from registry.opentofu.org"
  remote       = pulp_remote.opentofu.pulp_href
}

resource "pulp_distribution" "providers" {
  content_type = "tofu"
  plugin_name  = "tofu"
  name         = "providers"
  base_path    = "providers"
  repository   = pulp_repository.providers.pulp_href
  remote       = pulp_remote.opentofu.pulp_href
}
```

The change also lets a tofu distribution take a `content_guard`.

## Limits

The provider's remote resource is generic, so it does not set the fields only tofu
remotes have, such as `includes`, `metadata_ttl` or `require_signatures`. Set those
through the REST API or pulp-cli-tofu. The provider has no resources for namespaces,
registry tokens or signing services.
