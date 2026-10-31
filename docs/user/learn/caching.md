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

## HTTP caching of registry responses

Small registry documents (service discovery, version lists, download documents,
checksums and their signatures) are also cached by HTTP rules, as a shared cache would cache them. `Cache-Control: max-age`
or `Expires` sets how long a response stays fresh. A `no-store` or `private` response is
not kept. A `no-cache` response, or one with no freshness, is kept and revalidated with
`If-None-Match` or `If-Modified-Since`, so an unchanged listing costs the registry a
304 rather than a body. A 404 is kept for its `max-age`, at most 60 seconds. Pulp caps
every lifetime, so a registry that marks a listing fresh for a year cannot hide new
releases.

## Retries

Pulp retries a registry request or archive download that failed with 429, a 5xx status
or a transport error. It waits as long as `Retry-After` asks, and gives up when the
registry asks for more than 30 seconds, because a client is waiting. Otherwise it
waits a random time below a ceiling that starts at half a second and doubles with
each failure, up to 10 seconds.

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
