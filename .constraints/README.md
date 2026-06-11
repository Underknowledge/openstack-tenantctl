# OpenStack upper-constraints

Verbatim copies of the official per-release pin lists from the
[openstack/requirements](https://opendev.org/openstack/requirements) repo
(each file's header records its exact source URL). They pin the dependency
versions that coordinated OpenStack releases actually ship — most importantly
`openstacksdk` — so CI tests against the environments real deployments run,
not against whatever pip resolves on the day the job happens to execute.

Only releases whose pinned `openstacksdk` falls inside the range declared in
`pyproject.toml` (`>=4.0,<5.0`) are kept here; older releases pin 3.x or 0.x
and cannot resolve against this package:

| file          | release | openstacksdk |
|---------------|---------|--------------|
| dalmatian.txt | 2024.2  | 4.0.1        |
| epoxy.txt     | 2025.1  | 4.4.0        |
| flamingo.txt  | 2025.2  | 4.7.2        |
| gazpacho.txt  | 2026.1  | 4.10.0       |

## How CI uses them

Each matrix job installs in two steps:

```sh
pip install -e . -c .constraints/<release>.txt   # runtime deps pinned by the release
pip install -e ".[dev]"                          # our dev tooling, unconstrained
```

The split is deliberate: upper-constraints pin test tools too old for us
(e.g. `pytest-cov===4.1.0` vs our `>=6.0`), and pip constraints only apply to
the invocation they are passed to. The second step cannot move `openstacksdk`
because the pinned version already satisfies the pyproject range.

An additional unconstrained "latest" job (non-blocking on PRs, weekly cron)
catches SDK releases newer than any coordinated release early — that tier is
where 4.15-style annotation and signature churn shows up first.

## Refreshing / adding a release

Fetch the raw file from openstack/requirements — branch for active releases,
`-eol` tag once a release reaches end of life — keep the two header comment
lines, and update the table above and the CI matrix:

```sh
curl -fsSL https://opendev.org/openstack/requirements/raw/branch/stable/<series>/upper-constraints.txt
```

(`-L` matters: refs containing `/` are answered with a 303 redirect; without
it you silently save an HTML stub.) Drop a release from the matrix when its
pinned `openstacksdk` leaves the supported pyproject range, and bump the range
floor at the same time.
