# Publish Modules

!!! warning "Tech preview"
    Modules are a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

A namespace that accepts provider releases also accepts module packages. OpenTofu then
installs them by registry address:

```hcl
module "vpc" {
  source  = "pulp.example/acme/vpc/aws"
  version = "~> 1.0"
}
```

OpenTofu accepts a module registry hostname only if it contains a dot, so
`TOFU_REGISTRY_HOSTNAME` must be a dotted name for modules, even though providers work
with an undotted one.

## Publish a version

A package is a `.tar.gz` of the module's directory:

```bash
tar -czf vpc.tar.gz -C modules/vpc .
curl -u ci -F name=vpc -F system=aws -F version=1.0.0 -F file=@vpc.tar.gz \
  "$PULP<namespace pulp_href>publish_module/"
```

| field | rule |
| --- | --- |
| `name` | letters, digits, dashes and underscores, at most 64, no dash or underscore at either end; case-sensitive |
| `system` | the target system, lowercase letters and digits, e.g. `aws` |
| `version` | SemVer 2.0 without build metadata |

The task refuses a package that is not a gzip-compressed tar, that holds anything but
regular files and directories, or whose paths are absolute or climb out with `..`.
Publishing a version again with the same package changes nothing; with a different
package it answers 409.

Publishing needs the `tofu.publish_tofunamespace` permission, as for providers, and a
registry token with `publish` scope works in place of a password. Downloads of a
private namespace's modules follow [Run a Private Registry](site:pulp_tofu/docs/user/guides/private/):
with a `ContentRedirectContentGuard`, the location the registry returns is signed.
