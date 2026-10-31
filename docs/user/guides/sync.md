# Sync Providers Ahead of Use

Sync fetches chosen providers into a new repository version without waiting for a client
to ask. Registries publish no list of all providers, so a sync names the providers it
wants.

```bash
curl -u admin --json '{
  "remote": "<remote pulp_href>",
  "providers": [
    {"namespace": "hashicorp", "type": "aws", "latest": 2},
    {"namespace": "hashicorp", "type": "random", "versions": ["3.7.2"]}
  ],
  "platforms": ["linux_amd64", "darwin_arm64"]
}' "$PULP/pulp/api/v3/repositories/tofu/tofu/<repository id>/sync/"
```

| field | meaning |
| --- | --- |
| `providers[].versions` | Exact versions. Empty selects the newest releases. |
| `providers[].latest` | How many of the newest releases to fetch when `versions` is empty. Default 1. Pre-releases are skipped. |
| `platforms` | `os_arch` platforms whose archives to fetch. Empty fetches all. |
| `mirror` | `true` removes from the repository what this sync did not select. |

Every selected version's `SHA256SUMS` signature is verified during the sync. Under the
remote's `immediate` policy the archives download during the sync and get `h1:` hashes;
under `on_demand` they download on first request.

## Offline use

Sync with an `immediate` remote, then serve the repository through a distribution with
no remote:

```bash
curl -u admin --json '{
  "name": "tofu-offline",
  "base_path": "tofu-offline",
  "repository": "<repository pulp_href>"
}' "$PULP/pulp/api/v3/distributions/tofu/tofu/"
```

That distribution answers only from the repository and never contacts the registry.
