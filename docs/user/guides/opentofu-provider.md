# Manage Pulp with OpenTofu

You can declare Pulp's own objects in OpenTofu or Terraform code, and let `tofu apply`
create and update them, the same way you manage cloud resources. This takes a provider
for Pulp.

That provider is
[terraform-provider-pulp](https://github.com/e-breuninger/terraform-provider-pulp),
published in the Terraform registry as `e-breuninger/pulp`. It is a third-party
project, maintained by the e-breuninger organization on GitHub rather than by the Pulp
Project. It has generic resources for remotes, repositories, distributions, content
guards, users and roles, and a resource works with a plugin only if the provider lists
that plugin.

!!! warning "Needs an unreleased provider change"
    The provider does not yet list the `tofu` plugin. Until a change that adds it is
    merged and released, the `pulp_remote`, `pulp_repository` and `pulp_distribution`
    resources refuse `content_type = "tofu"`. To use them now, build the provider with
    that change, and point OpenTofu at the build with a `dev_overrides` block.

With `tofu` in its plugin list, the provider manages a pull-through mirror:

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
