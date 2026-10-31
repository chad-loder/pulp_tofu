# Roles and Permissions

pulp_tofu's objects use Pulp's role-based access control. Each type has locked roles,
assigned per object with `add_role` or for a whole domain.

## Namespaces

| role | grants |
| --- | --- |
| `tofu.tofunamespace_owner` | view, change, delete, publish, manage roles |
| `tofu.tofunamespace_collaborator` | view, publish |
| `tofu.tofunamespace_consumer` | view |
| `tofu.tofunamespace_creator` | create namespaces |

Viewing a private namespace is what reading it through the registry needs. Publishing
providers or modules needs `tofu.publish_tofunamespace` on the namespace. Creating or
editing a namespace also needs view permission on the distribution and the keyring it
names.

## Remotes, repositories and distributions

Each has `owner`, `viewer` and `creator` roles, as in other plugins:
`tofu.tofuremote_owner`, `tofu.tofurepository_owner`, `tofu.tofudistribution_owner`
and so on. A repository owner can also sync, modify and repair it.

## Registry tokens

A token authenticates as the account that created it and never adds a permission that
account lacks: it narrows the account's permissions to the token's namespaces, and to its
scope. `read` only reads, `update` also publishes new versions of names the namespace
serves, `publish` also publishes new names. Every account, administrators included, sees
and deletes only its own tokens.

A global role or a role on the domain counts as much as a role on the object wherever the
plugin checks a permission itself: reading a
private namespace, and naming namespaces, distributions and keyrings when creating a
token or a namespace.

## Workload Identity

From pulpcore 3.120, Workload Identity grants roles per request from an OIDC token's
claims. Grant a CI job `tofu.tofunamespace_consumer` to read or
`tofu.tofunamespace_collaborator` to publish, and a role carrying `core.view_task` so it
can follow the tasks it starts.
