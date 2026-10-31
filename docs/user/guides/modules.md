# Publish Modules

!!! warning "Tech preview"
    Modules are a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

A namespace that accepts provider releases also accepts module packages. OpenTofu
installs them by registry address:

```hcl
module "vpc" {
  source  = "pulp.example/acme/vpc/aws"
  version = "~> 1.0"
}
```

OpenTofu accepts a module registry hostname only if it contains a dot. For modules,
`TOFU_REGISTRY_HOSTNAME` must therefore be a dotted name, even though providers work
with an undotted one.

## Publish a version

A package is a `.tar.gz` of the module's directory.

To publish version 1.0.0 of the `vpc` module for `aws` to `acme`:

=== "pulp-cli"

    ```bash
    tar -czf vpc.tar.gz -C modules/vpc .
    pulp tofu namespace publish-module --name acme --module vpc --system aws \
      --version 1.0.0 vpc.tar.gz
    ```

=== "REST API"

    ```bash
    tar -czf vpc.tar.gz -C modules/vpc .
    curl -u ci -F name=vpc -F system=aws -F version=1.0.0 -F file=@vpc.tar.gz \
      "$PULP<namespace pulp_href>publish_module/"
    ```

| field | rule |
| --- | --- |
| `name` | Letters, digits, dashes and underscores, at most 64, with no dash or underscore at either end. Pulp compares names ignoring case, and refuses a name that differs from an existing one only in case. |
| `system` | The target system: lowercase letters and digits, for example `aws`. |
| `version` | SemVer 2.0, without build metadata. |

Pulp refuses a package in these cases:

- It is not a gzip-compressed tar.
- It holds anything other than regular files and directories.
- A path in it is absolute, or climbs out of the package with `..`.

If you publish a version again with the same package, nothing changes. If the package
differs, Pulp answers 409.

To publish, a user needs the `tofu.publish_tofunamespace` permission, as for providers.
A registry token also works in place of a password. A new module name needs a token
with `publish` scope, and a new version of an existing module needs `update` scope.

For a private namespace, module downloads work as described in
[Run a Private Registry](site:pulp_tofu/docs/user/guides/private/). With a
`ContentRedirectContentGuard`, the registry returns a signed download location.
