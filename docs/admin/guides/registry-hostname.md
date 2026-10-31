# Registry Hostname

!!! warning "Tech preview"
    The registry is a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

OpenTofu finds a registry by fetching `https://<hostname>/.well-known/terraform.json`.
The discovery document has no path, so a host serves one registry; namespaces route
requests inside it, as pulp_container routes one `/v2/` API by repository path.

## Settings

```python
TOFU_REGISTRY_ENABLED = True
TOFU_REGISTRY_HOSTNAME = "pulp.example"
```

`TOFU_REGISTRY_HOSTNAME` is the hostname users write in source addresses, with a port if
it is not 443. It must be the host that serves Pulp's API workers over TLS. Archive,
checksum and signature URLs in download documents start with `CONTENT_ORIGIN`, so that
must be reachable by OpenTofu clients as well. Every setting is listed in the
[settings reference](site:pulp_tofu/docs/admin/reference/settings/).

## Webserver

The registry runs in the API workers. The shipped snippets route two locations to them:

- `location = /.well-known/terraform.json`, an exact match, so it wins over a
  `/.well-known/` prefix block such as the ACME one in Pulp's container images;
- `location /tofu/`, the `TOFU_REGISTRY_PATH_PREFIX`. Change both together.

Include the nginx snippet in the server block for `TOFU_REGISTRY_HOSTNAME`; see
[Install the Plugin](site:pulp_tofu/docs/admin/guides/install/) for where it lives.

## Domains

With `DOMAIN_ENABLED`, the registry serves the domain named by `TOFU_REGISTRY_DOMAIN`,
below `/tofu/<domain>/v1/providers/`. One host still serves one domain's registry.

## Authentication

Registry requests authenticate with pulpcore's `DEFAULT_AUTHENTICATION_CLASSES`, after
any classes listed in `TOFU_AUTHENTICATION_CLASSES`. OpenTofu sends the credential in
`TF_TOKEN_<hostname>` as `Authorization: Bearer`, so a public registry needs nothing,
and a private namespace needs an authentication class that accepts bearer tokens.
