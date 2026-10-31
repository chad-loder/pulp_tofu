# Run a Private Registry

!!! warning "Tech preview"
    Registry tokens are a [tech preview](site:pulp_tofu/docs/user/learn/tech-preview/).

A private namespace answers registry requests only from users allowed to view it. OpenTofu
authenticates with a bearer token read from `TF_TOKEN_<hostname>`, with dots in the
hostname written as underscores.

## Create a token

A user creates a token for the namespaces it should reach. Pulp shows the token once.

To create a token and hand it to OpenTofu:

```bash
curl -u alice --json '{
  "name": "laptop",
  "namespaces": ["<namespace pulp_href>"],
  "scope": "read",
  "expires_at": "2027-01-01T00:00:00Z"
}' "$PULP/pulp/api/v3/pulp_tofu/tokens/"
export TF_TOKEN_pulp_example="tofu_..."
```

A token acts as its user, narrowed to its namespaces and to its scope:

| scope | allows |
| --- | --- |
| `read` | reading the namespaces through the registry |
| `update` | also publishing new versions of providers and modules the namespace already serves |
| `publish` | also publishing new providers and modules |

A release job can use an `update` or `publish` token in place of a password. Give it
`update` once its provider or module is published: if the token leaks, it cannot add a
lookalike name to the namespace. Pulp stores only a digest of the token. Delete the
token to revoke it; every user sees and deletes only their own tokens.

## Authenticate CI with Workload Identity

From pulpcore 3.120, pulpcore's Workload Identity accepts a CI provider's short-lived OIDC
token as the bearer token and grants roles per request from claim rules. Once an
administrator enables it in pulpcore's settings, the registry and the namespace `publish`
action accept those tokens with nothing further to configure here. Grant the job
`tofu.tofunamespace_consumer` to read or `tofu.tofunamespace_collaborator` to publish.

## Guard archive downloads

OpenTofu sends no credentials when it downloads archives, checksums and signatures. A
private namespace's archive URLs are readable by anyone who knows them unless the
distribution has a content guard. Use a `ContentRedirectContentGuard`: the registry then
answers with URLs the guard has signed, valid for one hour. Other content guards refuse
OpenTofu's downloads.

To guard a distribution's downloads:

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

Provider installs fail the same way. This issue occurs when OpenTofu sent no
credentials: `TF_TOKEN_<hostname>` is unset, or named for a different hostname. Dots in
the hostname are underscores in the variable name, so `pulp.example` reads
`TF_TOKEN_pulp_example`.

To resolve it, create a token for the namespace and export it under the hostname's
variable name. An answer of 403 instead means the token or user lacks read access to
the namespace.
