# Differences from registry.opentofu.org

`source` addresses and `tofu init` work the same way against Pulp as against
registry.opentofu.org. The two differ in how you publish and who can install:

| | registry.opentofu.org | Pulp |
| --- | --- | --- |
| Namespaces | GitHub organizations and users | namespaces you create in Pulp |
| Publishing | a pull request to the registry's repository, which then reads GitHub releases | an upload to Pulp, from CI or by hand |
| Where OpenTofu downloads providers | GitHub releases | Pulp |
| Where OpenTofu downloads modules | GitHub | Pulp |
| Signing keys | Optional. For this registry alone, OpenTofu skips the signature check when no key is listed. | Required, because OpenTofu requires a key from every other registry. Pulp does not serve a version without one. |
| Who can install | everyone | everyone, or for a private namespace, only users allowed to view it |
| Credentials | none | a registry token or Workload Identity |
| Name case | ignored | Ignored. Pulp refuses a module whose name differs from an existing one only in case. |
| Changing a published version | not possible | not possible |

Pulp can also serve providers that it syncs or pulls from registry.opentofu.org under its
own hostname. See the `content_hostname` field in
[Serve Providers as a Registry](site:pulp_tofu/docs/user/guides/registry/).
