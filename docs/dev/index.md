# Developing pulp_tofu

The repository follows [plugin_template](https://github.com/pulp/plugin_template); its
GitHub workflows are what Pulp's CI runs, and
[oci-env](https://github.com/pulp/oci_env) runs the same tests locally.

## Checks without Pulp

`uv sync` installs the `lint` and `test` dependency groups from `pyproject.toml`.

| command | checks |
| --- | --- |
| `uv run ruff check` and `uv run ruff format --check` | what CI's lint job checks |
| `PYTHONPATH=dev uv run mypy pulp_tofu` and `uv run pyright` | strict typing, configured in `pyproject.toml` and `pyrightconfig.json` |
| `uv run pytest pulp_tofu/tests/unit` | unit tests |

`dev/pulp_dev_settings.py` lets the Django mypy plugin load pulpcore's settings
outside a deployment.

## Functional tests

The functional tests run inside the Pulp container, as the `pulp` user, as CI runs
them:

- pulpcore's fixtures reach Pulp at `API_PROTOCOL://API_HOST:API_PORT` as
  `ADMIN_USERNAME` with `ADMIN_PASSWORD`;
- Pulp's webserver certificate is in certifi's bundle, and the generated bindings
  trust it too;
- `pulpcore-manager` registers the signing service the publish tests use;
- Pulp's settings have `TOFU_REGISTRY_ENABLED = True` and
  `TOFU_REGISTRY_HOSTNAME = "pulp.test"` (`template_config.yml` sets both for CI).

`test_tofu_init.py` runs the OpenTofu 1.12.5 release binary, downloaded from GitHub and
checked against its SHA-256, against Pulp as a network mirror and as a registry. It
reaches registry.opentofu.org and github.com. OpenTofu needs a dotted registry
hostname, so a proxy in the test process answers `CONNECT pulp.test:443` with its own
certificate and relays to Pulp.

Tests marked `parallel` run across pytest-xdist workers and the rest run after them in
one process:

```sh
pytest -r sx --timeout=300 --pyargs pulp_tofu.tests.functional -m parallel -n 8
pytest -r sx --timeout=300 --pyargs pulp_tofu.tests.functional -m "not parallel"
```

Every test that can be is parallel-safe: each uses its own remote, repository,
distribution and provider type, and each worker runs its own fake registry. The
`tofu init` tests are not, because the registry namespaces they serve (`hashicorp`,
`cloudposse`) are global.

The fake registry (`pulp_tofu/tests/functional/fake_registry.py`) signs its own
providers and can truncate, corrupt, stall or refuse responses, and reap idle
connections by closing, resetting or silently dropping them.

### With oci-env

With `oci_env`, `pulpcore` and this repository checked out side by side as
`pulp_tofu`, a `compose.env` like this serves Pulp over HTTPS as `pulp`:

```sh
COMPOSE_BINARY=docker
DEV_SOURCE_PATH=pulpcore:pulp_tofu
API_PROTOCOL=https
API_HOST=pulp
API_PORT=5443
NGINX_PORT=5001
NGINX_SSL_PORT=5443
PULP_HTTPS=true
PULP_SECRET_KEY=<random>
PULP_TOFU_REGISTRY_ENABLED=true
PULP_TOFU_REGISTRY_HOSTNAME=pulp.test
SSL_CERT_FILE=/usr/local/lib/python3.12/site-packages/certifi/cacert.pem
```

`SSL_CERT_FILE` points the bindings at certifi's bundle. After `oci-env compose up`,
add the certificate to it, as CI does:

```sh
oci-env exec sh -c 'cat /etc/pulp/certs/pulp_webserver.crt >> "$(python3 -c "import certifi; print(certifi.where())")"'
```

`oci-env generate-client` fetches the API schema from the host, where `pulp` does not
resolve. Generate the bindings as CI does, from
`pulpcore-manager openapi --bindings --component tofu` inside the container and
[pulp-openapi-generator](https://github.com/pulp/pulp-openapi-generator)'s
`gen-client.sh`, then install them in the container with `pulpcore-client` at the
running pulpcore version. Then:

```sh
oci-env test -i -p pulp_tofu functional -- --timeout=300 -m parallel -n 8
oci-env test -p pulp_tofu functional -- --timeout=300 -m "not parallel"
```

The API and content servers reload when the checkout changes. After changing task
code, restart the workers with `oci-env prestart`.
