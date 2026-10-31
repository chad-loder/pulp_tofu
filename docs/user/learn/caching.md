# Caching and Upstream Load

Pulp keeps three kinds of upstream data, and refreshes each on its own schedule.

| data | stored as | refreshed |
| --- | --- | --- |
| A provider's version list | a cached listing per remote | after `metadata_ttl` seconds, in the background |
| A version's signed checksums | `ProviderVersion` content | never, because a published version does not change |
| An archive | `ProviderPackage` content with an artifact | never |

## Version lists

When a cached list is older than `metadata_ttl`, Pulp answers from it at once and fetches
a fresh list in the background. A client waits for the registry only for a provider
Pulp has never listed. If the registry fails, Pulp keeps serving the last list.

When a registry answers that a provider does not exist, Pulp remembers the answer for
60 seconds. Retrying clients then do not each reach the registry.

## HTTP caching of registry responses

Pulp also caches small registry documents by HTTP rules, as a shared cache would. These
documents are service discovery, version lists, download documents, and checksums with
their signatures.

- `Cache-Control: max-age` or `Expires` sets how long Pulp treats a response as fresh.
- Pulp does not keep a `no-store` or `private` response.
- Pulp keeps a `no-cache` response, or one with no freshness information, and
  revalidates it with `If-None-Match` or `If-Modified-Since`. An unchanged listing then
  costs the registry a 304 instead of a full body.
- Pulp keeps a 404 for its `max-age`, at most 60 seconds.

Pulp caps every lifetime, so a registry that marks a listing fresh for a year cannot
hide new releases.

## Retries

Pulp retries a registry request or an archive download that fails with 429, with a 5xx
status, or with a transport error:

- If the registry sends `Retry-After`, Pulp waits that long. If the registry asks for
  more than 30 seconds, Pulp gives up, because a client is waiting.
- Otherwise, Pulp waits a random time below a ceiling. The ceiling starts at half a
  second and doubles with each failure, up to 10 seconds.

## Concurrent first requests

When many clients ask for the same uncached provider at once, each Pulp content worker
sends one request upstream. Its other clients wait for that request and share the
result. This applies to version lists, signature checks and archive downloads.

## Interrupted downloads

If the upstream connection drops partway through an archive, Pulp can resume the
download. It sends a `Range` request starting at the last byte it sent the client, and
the client's download continues as one stream. Pulp can resume only if the upstream
offered a strong `ETag` or a `Last-Modified` date.

Without either, the client's download fails and Pulp stores nothing. The client's retry
then starts from the beginning. Attempts that make progress do not count against the
remote's `max_retries`.

If no byte arrives for 60 seconds, Pulp treats the download as stalled and retries it.
A remote's `sock_read_timeout` replaces the 60 seconds. A download has no overall
deadline unless the remote sets `total_timeout`, so a slow, large archive keeps going.

## Connections

Registry requests from one content worker share kept-alive connections. When the
registry or the network closes an idle connection, Pulp opens a new one on the next
request, and does not count the closed connection as a failure.
