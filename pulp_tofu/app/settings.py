"""Settings for pulp_tofu, merged into Pulp's settings. Each can be overridden there."""

# Serve the provider registry protocol and its discovery document at this Pulp's host
# root. Off, /.well-known/terraform.json answers 404 and nothing else changes.
TOFU_REGISTRY_ENABLED = False

# The hostname, with a port if not 443, that OpenTofu users write in provider source
# addresses for this registry. Content published to Pulp carries it as its origin.
TOFU_REGISTRY_HOSTNAME = None

# With domains enabled, the domain the registry serves; discovery names one per host.
TOFU_REGISTRY_DOMAIN = "default"

# URL path prefix of the registry API, served by the API workers.
TOFU_REGISTRY_PATH_PREFIX = "/tofu/"

# Authentication classes for the registry API, tried before pulpcore's defaults.
TOFU_AUTHENTICATION_CLASSES: list[str] = []
