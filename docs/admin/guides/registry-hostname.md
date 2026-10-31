# Set the Registry Hostname

!!! warning "Tech preview"
    The registry is a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

OpenTofu finds a registry by fetching `https://<hostname>/.well-known/terraform.json`.
The discovery document has no path, so each host serves one registry. Inside it,
namespaces route the requests, as pulp_container routes one `/v2/` API by repository
path.

## Turn on the registry

To turn on the registry, add to Pulp's settings:

```python
TOFU_REGISTRY_ENABLED = True
TOFU_REGISTRY_HOSTNAME = "pulp.example"
```

`TOFU_REGISTRY_HOSTNAME` is the hostname users write in source addresses, with a port if
it is not 443. It has three requirements:

- It contains a dot. OpenTofu refuses a module registry hostname without one.
- It is the host that serves Pulp's API workers over TLS.
- OpenTofu clients can also reach `CONTENT_ORIGIN`, because the archive, checksum and
  signature URLs in download documents start with it.

The [settings reference](site:pulp_tofu/docs/admin/reference/settings/) lists every
setting.

## Route the registry

The registry runs in the API workers. The shipped snippets route two locations to them:

- `location = /.well-known/terraform.json`. This exact match wins over any
  `/.well-known/` prefix block, such as the ACME block in Pulp's container images.
- `location /tofu/`, the `TOFU_REGISTRY_PATH_PREFIX`. If you change the prefix, change
  this location with it.

Include the nginx snippet in the server block for `TOFU_REGISTRY_HOSTNAME`. For where the
snippet lives, see [Install the Plugin](site:pulp_tofu/docs/admin/guides/install/).

## Domains

With `DOMAIN_ENABLED`, the registry serves the domain named by `TOFU_REGISTRY_DOMAIN`,
below `/tofu/<domain>/v1/providers/`. One host still serves only one domain's registry.

## Authentication

Registry requests authenticate first with any classes in `TOFU_AUTHENTICATION_CLASSES`,
then with pulpcore's `DEFAULT_AUTHENTICATION_CLASSES`. OpenTofu sends the credential from
`TF_TOKEN_<hostname>` as `Authorization: Bearer`. A public registry needs no
authentication. A private namespace needs an authentication class that accepts bearer
tokens.
