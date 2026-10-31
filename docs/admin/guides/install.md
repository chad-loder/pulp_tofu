# Install the Plugin

pulp_tofu needs pulpcore 3.116 or later and below 3.130, on Python 3.12 or later.

```bash
pip install pulp-tofu
pulpcore-manager migrate tofu
```

Restart the API, content and worker processes so they load the plugin.

## Webserver

The mirror is served by the content app below `/pulp/tofu/`, so the webserver in front
of Pulp must route that path to it. The package ships snippets for both webservers Pulp
supports:

- nginx: `pulp_tofu/app/webserver_snippets/nginx.conf`, included from the server block
  that proxies to `pulp-content`. In Pulp's container images, link it into
  `/etc/nginx/pulp/`.
- Apache: `pulp_tofu/app/webserver_snippets/apache.conf`.

The nginx snippet turns off response buffering, so archives stream to the client while
Pulp downloads them, and proxies over HTTP/1.1. Over HTTP/1.0 an archive download that
Pulp aborts reaches the client as a complete-looking response.

## TLS

OpenTofu requires `https` for a network mirror, and archive downloads carry no
credentials. Serve Pulp's content app over TLS, and keep content guards on tofu
distributions open to anonymous reads.

## Domains

The plugin supports domains. With `DOMAIN_ENABLED`, mirrors are served below
`/pulp/tofu/<domain>/<base_path>/`.
