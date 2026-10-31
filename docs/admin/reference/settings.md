# Settings

pulp_tofu adds these settings to Pulp's. Set them as any other Pulp setting.

| setting | default | meaning |
| --- | --- | --- |
| `TOFU_REGISTRY_ENABLED` | `False` | Serve the provider registry protocol and `/.well-known/terraform.json`. Off, both answer 404. |
| `TOFU_REGISTRY_HOSTNAME` | `None` | The hostname, with a port if not 443, that users write in source addresses. Namespaces without a `content_hostname` serve content whose origin is this host. |
| `TOFU_REGISTRY_DOMAIN` | `"default"` | With `DOMAIN_ENABLED`, the domain the registry serves. |
| `TOFU_REGISTRY_PATH_PREFIX` | `"/tofu/"` | Path of the registry API. The webserver snippets route `/tofu/`; change them with it. |
| `TOFU_AUTHENTICATION_CLASSES` | `[]` | Dotted paths of DRF authentication classes tried on registry requests before pulpcore's defaults. |
