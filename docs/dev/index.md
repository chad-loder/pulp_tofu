# Developing pulp_tofu

The repository follows [plugin_template](https://github.com/pulp/plugin_template); its
GitHub workflows are what Pulp's CI runs. A `justfile` drives local work:

| recipe | runs |
| --- | --- |
| `just deps` | `uv sync` from `uv.lock` |
| `just lint` | ruff, mypy strict and pyright strict |
| `just test` | unit tests, no Pulp needed |
| `just e2e` | builds Pulp's image with this checkout, then runs `tofu init` through it twice |
| `just bindings` | generates the Python bindings from that Pulp with Pulp's generator |
| `just functional` | functional tests against that Pulp and an in-process fake registry |

The fake registry (`pulp_tofu/tests/functional/fake_registry.py`) signs its own
providers and can truncate, corrupt, stall or refuse responses, and reap idle
connections by closing, resetting or silently dropping them.

`dev/pulp_dev_settings.py` lets the Django mypy plugin and pulpcore's test fixtures load
pulpcore's settings outside a deployment.
