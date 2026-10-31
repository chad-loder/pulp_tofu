# Caching and Upstream Load

Pulp keeps three kinds of upstream data, each with its own freshness rule.

| data | stored as | refreshed |
| --- | --- | --- |
| A provider's version list | a cached listing per remote | after `metadata_ttl` seconds, in the background |
| A version's signed checksums | `ProviderVersion` content | never; a published version does not change |
| An archive | `ProviderPackage` content with an artifact | never |

## Version lists

When a cached list is older than `metadata_ttl`, Pulp answers from it at once and fetches
a fresh one in the background. Only a provider never listed before waits for the
registry. If the registry fails, the last list keeps being served. A registry's "no such
provider" is remembered for 60 seconds, so retrying clients do not each reach the
registry.

## Concurrent first requests

When many clients ask for the same uncached provider at once, one request per Pulp
content worker goes upstream; the others wait for it and share the result. That holds
for version lists, signature checks and archive downloads.

## Interrupted downloads

If the connection to the upstream drops partway through an archive, Pulp resumes it with
a `Range` request from the last byte it sent the client, provided the upstream offered a
strong `ETag` or a `Last-Modified` date. The client's download continues as one stream.
Without either, the client's download fails and nothing is stored; the client's retry
starts afresh. Attempts that make progress do not use up the remote's `max_retries`.

A download with no byte for 60 seconds counts as stalled and is retried, unless the
remote sets `sock_read_timeout`. There is no overall deadline unless the remote sets
`total_timeout`, so a slow large archive keeps going.

## Connections

Registry requests from one content worker share kept-alive connections. A connection the
registry or the network closed while idle is replaced on the next request without
counting as a failure.
