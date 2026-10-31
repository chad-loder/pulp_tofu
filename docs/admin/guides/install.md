# Install the Plugin

Prerequisites:

- pulpcore 3.116 or later, and below 3.130
- Python 3.12 or later

pulp_tofu is not on PyPI yet. Install it from git, into the same environment as
pulpcore:

```bash
pip install "pulp-tofu @ git+https://github.com/chad-loder/pulp_tofu"
pulpcore-manager migrate tofu
```

Then restart the API, content and worker processes, so they load the plugin.

## Webserver

Pulp's content app serves the mirror below `/pulp/tofu/`, so the webserver in front of
Pulp must route that path to the content app. The package ships snippets for both
webservers Pulp supports:

- nginx: `pulp_tofu/app/webserver_snippets/nginx.conf`. Include it from the server block
  that proxies to `pulp-content`. In Pulp's container images, link it into
  `/etc/nginx/pulp/`.
- Apache: `pulp_tofu/app/webserver_snippets/apache.conf`.

The nginx snippet turns off response buffering, so archives stream to the client while
Pulp downloads them. It also proxies over HTTP/1.1. Over HTTP/1.0, an archive download
that Pulp aborts would reach the client looking complete.

The snippets also route the registry to the API workers: `/.well-known/terraform.json`,
and `/tofu/`, the default `TOFU_REGISTRY_PATH_PREFIX`. Pulp answers these paths only
with `TOFU_REGISTRY_ENABLED`. See
[Set the Registry Hostname](site:pulp_tofu/docs/admin/guides/registry-hostname/).

## TLS

OpenTofu requires `https` for a network mirror, so serve Pulp's content app over TLS.
OpenTofu also sends no credentials when it downloads archives. A content guard on a
tofu distribution must therefore allow anonymous reads, or be a
`ContentRedirectContentGuard`, which signs the download URLs.

## Domains

The plugin supports domains. With `DOMAIN_ENABLED`:

- Pulp serves mirrors below `/pulp/tofu/<domain>/<base_path>/`.
- The registry serves only the domain named by `TOFU_REGISTRY_DOMAIN`, below
  `/tofu/<domain>/v1/`.

Service discovery names these paths, so source addresses stay the same.

## Object storage

Pulp can keep content in S3 or Azure storage, configured in `STORAGES`. With
`REDIRECT_TO_OBJECT_STORAGE`, which is on by default, archive and module downloads
redirect to signed storage URLs, and OpenTofu follows them. Every OpenTofu client must
then be able to reach the storage endpoint.
