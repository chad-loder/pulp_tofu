# Differences from registry.opentofu.org

Pulp answers the same registry protocol as registry.opentofu.org, so `source` addresses
and `tofu init` work the same way. What sits behind the protocol differs:

| | registry.opentofu.org | Pulp |
| --- | --- | --- |
| Namespaces | GitHub organizations and users | `TofuNamespace` objects, each served by a distribution |
| Publishing | a pull request to the registry's repository, read from GitHub releases | an upload to the namespace's `publish` or `publish_module` action |
| Provider archives | downloaded from GitHub releases | served by Pulp's content app |
| Module packages | a `git::` location on GitHub | a `.tar.gz` served by Pulp's content app |
| Signing keys | optional; OpenTofu skips the signature check for this registry alone when none is listed | required; OpenTofu requires one from every other registry, so a version without a key is not served |
| Who can read | everyone | everyone, or only users allowed to view a private namespace |
| Credentials | none | a registry token or Workload Identity, sent as a bearer token |
| Name case | ignored | ignored; a module published under a name that differs from an existing one only in case is refused |
| Changing a published version | not possible | refused with 409 |

Pulp can also re-serve providers it syncs or pulls from registry.opentofu.org under its
own hostname: see the `content_hostname` field in
[Serve a Registry](site:pulp_tofu/docs/user/guides/registry/).
