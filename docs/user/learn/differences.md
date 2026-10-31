# Differences from registry.opentofu.org

Pulp speaks the same registry protocol as registry.opentofu.org, so `source` addresses
and `tofu init` work the same way. Behind the protocol, the two differ:

| | registry.opentofu.org | Pulp |
| --- | --- | --- |
| Namespaces | GitHub organizations and users | `TofuNamespace` objects, each served by a distribution |
| Publishing | a pull request to the registry's repository, which then reads GitHub releases | an upload to the namespace's `publish` or `publish_module` action |
| Provider archives | downloaded from GitHub releases | served by Pulp's content app |
| Module packages | a `git::` location on GitHub | a `.tar.gz` served by Pulp's content app |
| Signing keys | Optional. For this registry alone, OpenTofu skips the signature check when no key is listed. | Required, because OpenTofu requires a key from every other registry. Pulp does not serve a version without one. |
| Who can read | everyone | everyone, or for a private namespace, only users allowed to view it |
| Credentials | none | a registry token or Workload Identity, sent as a bearer token |
| Name case | ignored | Ignored. Pulp refuses a module whose name differs from an existing one only in case. |
| Changing a published version | not possible | refused with 409 |

Pulp can also serve providers that it syncs or pulls from registry.opentofu.org under its
own hostname. See the `content_hostname` field in
[Serve Providers as a Registry](site:pulp_tofu/docs/user/guides/registry/).
