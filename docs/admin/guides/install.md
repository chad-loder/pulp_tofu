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

## Configure the webserver

OpenTofu reaches Pulp through the webserver in front of it. The plugin ships a
ready-made config snippet for nginx and one for Apache. Add the one for your webserver:

=== "nginx"

    1. Print the snippet's path:

        ```bash
        python3 -c 'import pathlib, pulp_tofu.app as a; print(pathlib.Path(a.__file__).parent / "webserver_snippets/nginx.conf")'
        ```

    2. Include it in the `server` block that proxies to Pulp. In Pulp's container
       images, link it into `/etc/nginx/pulp/`, which that block already includes.
    3. Reload nginx.

=== "Apache"

    1. Print the snippet's path:

        ```bash
        python3 -c 'import pathlib, pulp_tofu.app as a; print(pathlib.Path(a.__file__).parent / "webserver_snippets/apache.conf")'
        ```

    2. Include it in the `VirtualHost` that defines `pulp-api` and `pulp-content`.
    3. Reload Apache.

The snippet also routes the registry, which stays off until you turn it on. See
[Set the Registry Hostname](site:pulp_tofu/docs/admin/guides/registry-hostname/).

### Write your own webserver config

If you route Pulp with your own config instead of the snippet, it needs these
locations:

| path | send to | why |
| --- | --- | --- |
| `/pulp/tofu/` | content app | Mirror downloads. Turn off response buffering, so OpenTofu receives an archive while Pulp is still fetching it. Proxy over HTTP/1.1: over HTTP/1.0, a download that Pulp aborts reaches OpenTofu looking complete. |
| `/.well-known/terraform.json` | API | Registry discovery. In nginx, use an exact-match location, so it wins over any `/.well-known/` block, such as the ACME block in Pulp's container images. |
| `/tofu/` | API | The registry. Change it together with `TOFU_REGISTRY_PATH_PREFIX`. |

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
