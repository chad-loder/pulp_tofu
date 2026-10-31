# Roles and Permissions

pulp_tofu's objects use Pulp's role-based access control. Each object type has locked
roles. Assign them per object with `add_role`, or for a whole domain.

## Namespaces

| role | grants |
| --- | --- |
| `tofu.tofunamespace_owner` | view, change, delete, publish, manage roles |
| `tofu.tofunamespace_collaborator` | view, publish |
| `tofu.tofunamespace_consumer` | view |
| `tofu.tofunamespace_creator` | create namespaces |

- To read a private namespace through the registry, a user needs view permission on it.
- To publish providers or modules, a user needs `tofu.publish_tofunamespace` on the
  namespace.
- To create or edit a namespace, a user also needs view permission on the distribution
  and the keyring it names.

## Remotes, repositories and distributions

As in other plugins, each type has `owner`, `viewer` and `creator` roles:
`tofu.tofuremote_owner`, `tofu.tofurepository_owner`, `tofu.tofudistribution_owner`, and
so on. A repository owner can also sync, modify and repair the repository.

## Registry tokens

A token authenticates as the account that created it. It never grants a permission the
account lacks. Instead, it limits the account's permissions to the token's namespaces
and scope:

- `read` only reads.
- `update` also publishes new versions of names the namespace already serves.
- `publish` also publishes new names.

Every account, administrators included, sees and deletes only its own tokens.

In the permission checks the plugin makes itself, a global role or a role on the domain
counts the same as a role on the object. These checks cover reading a private namespace,
and naming namespaces, distributions and keyrings when creating a token or a namespace.

## Workload Identity

From pulpcore 3.120, Workload Identity grants roles for each request from an OIDC
token's claims. Grant a CI job `tofu.tofunamespace_consumer` to read, or
`tofu.tofunamespace_collaborator` to publish. Also grant it a role with `core.view_task`,
so it can follow the tasks it starts.
