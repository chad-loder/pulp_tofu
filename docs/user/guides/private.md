# Run a Private Registry

!!! warning "Tech preview"
    Registry tokens are a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

A private namespace answers registry requests only from users allowed to view it.
OpenTofu authenticates with a bearer token that it reads from `TF_TOKEN_<hostname>`.
In the variable name, dots in the hostname become underscores.

## Create a token

Each user creates their own tokens, for the namespaces they need. Pulp shows a token
only once, when it creates it.

To create a token for the namespace `acme` and hand it to OpenTofu:

=== "pulp-cli"

    ```bash
    namespace=$(pulp tofu namespace show --name acme | jq -r .pulp_href)
    pulp tofu token create --name laptop --namespace "$namespace" --scope read \
      --expires-at 2027-01-01T00:00:00Z
    export TF_TOKEN_pulp_example="tofu_..."
    ```

=== "REST API"

    ```bash
    curl -u alice --json '{
      "name": "laptop",
      "namespaces": ["<namespace pulp_href>"],
      "scope": "read",
      "expires_at": "2027-01-01T00:00:00Z"
    }' "$PULP/pulp/api/v3/pulp_tofu/tokens/"
    export TF_TOKEN_pulp_example="tofu_..."
    ```

A token acts as its user, limited to its namespaces and its scope:

| scope | allows |
| --- | --- |
| `read` | reading the namespaces through the registry |
| `update` | also publishing new versions of providers and modules the namespace already serves |
| `publish` | also publishing new providers and modules |

A release job can use an `update` or `publish` token in place of a password. Once the
job's provider or module is published, switch it to an `update` token. Then, if the
token leaks, nobody can use it to add a lookalike name to the namespace.

Pulp stores only a digest of each token. To revoke a token, delete it. Users see and
delete only their own tokens.

## Authenticate CI with Workload Identity

From pulpcore 3.120, pulpcore's Workload Identity accepts a CI provider's short-lived
OIDC token as the bearer token, and grants roles for each request from claim rules. An
administrator enables it in pulpcore's settings. The registry and the namespace
`publish` action then accept those tokens, with nothing more to configure in
pulp_tofu.

Grant the job `tofu.tofunamespace_consumer` to read, or
`tofu.tofunamespace_collaborator` to publish.

## Guard archive downloads

OpenTofu sends no credentials when it downloads archives, checksums and signatures.
Without a content guard on the distribution, anyone who knows a private namespace's
archive URLs can download them.

Use a `ContentRedirectContentGuard`. The registry then answers with URLs that the guard
has signed, valid for one hour. Other content guards refuse OpenTofu's downloads.

To guard the downloads from the distribution `acme`:

=== "pulp-cli"

    ```bash
    pulp content-guard redirect create --name tofu-redirect
    pulp tofu distribution update --name acme --content-guard core:content_redirect:tofu-redirect
    ```

=== "REST API"

    ```bash
    guard=$(curl -u admin --json '{"name": "tofu-redirect"}' \
      "$PULP/pulp/api/v3/contentguards/core/content_redirect/" | jq -r .pulp_href)
    curl -u admin -X PATCH --json '{"content_guard": "'"$guard"'"}' "$PULP<distribution pulp_href>"
    ```

## Troubleshooting

When OpenTofu reads a private namespace, you might encounter the following issues.

### Error: `... 401 Unauthorized.`

You might get an error that states:

```text
Error: Error accessing remote module registry
pulp.example: error looking up module versions: 401 Unauthorized.
```

Provider installs fail the same way. This issue occurs when OpenTofu sends no
credentials, because `TF_TOKEN_<hostname>` is unset or names a different hostname. In
the variable name, dots in the hostname become underscores: for `pulp.example`,
OpenTofu reads `TF_TOKEN_pulp_example`.

To resolve it, create a token for the namespace and export it under the hostname's
variable name. If Pulp answers 403 instead, the token or user has no read access to the
namespace.
