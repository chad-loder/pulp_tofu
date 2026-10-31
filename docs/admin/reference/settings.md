# Settings

pulp_tofu adds these settings to Pulp's. Set them as you set any other Pulp setting.

| setting | default | meaning |
| --- | --- | --- |
| `TOFU_REGISTRY_ENABLED` | `False` | Serve the provider registry protocol and `/.well-known/terraform.json`. When off, both answer 404. |
| `TOFU_REGISTRY_HOSTNAME` | `None` | The hostname users write in source addresses, with a port if it is not 443. A namespace without a `content_hostname` serves the content published to this host. |
| `TOFU_REGISTRY_DOMAIN` | `"default"` | With `DOMAIN_ENABLED`, the domain the registry serves. |
| `TOFU_REGISTRY_PATH_PREFIX` | `"/tofu/"` | The path of the registry API. The webserver snippets route `/tofu/`, so change them if you change this. |
| `TOFU_AUTHENTICATION_CLASSES` | `["pulp_tofu.app.authentication.TofuTokenAuthentication"]` | Dotted paths of DRF authentication classes. Pulp tries them on registry requests and namespace publishing, before pulpcore's defaults. To turn registry tokens off, remove the token class. |
