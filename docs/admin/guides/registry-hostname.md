# Set the Registry Hostname

Users write the registry hostname at the start of `source` addresses, as in
`pulp.example/hashicorp/aws`. Each hostname serves one registry. Namespaces inside it
keep teams and their providers apart.

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

The webserver snippet from
[Install the Plugin](site:pulp_tofu/docs/admin/guides/install/#configure-the-webserver)
already routes the registry. Include it in the server block that answers for
`TOFU_REGISTRY_HOSTNAME`.

To check that the registry answers:

```bash
curl https://pulp.example/.well-known/terraform.json
```

The response lists `providers.v1` and `modules.v1`. A 404 means the registry is off,
or the webserver does not route the path to Pulp.

## Domains

With `DOMAIN_ENABLED`, the registry serves the domain named by `TOFU_REGISTRY_DOMAIN`,
below `/tofu/<domain>/v1/providers/`. One host still serves only one domain's registry.

## Authentication

Registry requests authenticate first with any classes in `TOFU_AUTHENTICATION_CLASSES`,
then with pulpcore's `DEFAULT_AUTHENTICATION_CLASSES`. OpenTofu sends the credential from
`TF_TOKEN_<hostname>` as `Authorization: Bearer`. A public registry needs no
authentication. A private namespace needs an authentication class that accepts bearer
tokens.
