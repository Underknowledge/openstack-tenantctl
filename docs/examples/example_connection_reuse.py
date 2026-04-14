#!/usr/bin/env python3
"""Example demonstrating connection reuse with TenantCtl.

This example shows how to authenticate once and reuse the connection for both
custom operations and TenantCtl provisioning.
"""

import openstack
from src import TenantCtl, ProjectConfig

# 1. Authenticate once
print("1. Authenticating to OpenStack...")
conn = openstack.connect(cloud="devstack")
conn.authorize()

# 2. Do custom operations with the connection
print("2. Performing custom operations...")
servers = list(conn.compute.servers())
print(f"   Found {len(servers)} existing servers")

networks = list(conn.network.networks())
print(f"   Found {len(networks)} existing networks")

# 3. Use TenantCtl for provisioning with the same connection
print("3. Using TenantCtl for provisioning with same connection...")
client = TenantCtl.from_cloud()

projects = [
    ProjectConfig.build(
        name="team-a",
        resource_prefix="ta",
        network={"subnet": {"cidr": "10.0.0.0/24"}},
    ),
]

result = client.run(
    projects=projects,
    all_projects=projects,
    connection=conn,  # Reuse existing connection
    dry_run=True,  # Just validate, don't create
)

print(f"   TenantCtl would perform {len(result.actions)} actions")
for action in result.actions[:5]:  # Show first 5
    print(f"     {action.status}: {action.resource_type} {action.name}")

# 4. More custom operations after provisioning
print("4. More custom operations...")
servers_after = list(conn.compute.servers())
print(f"   Now have {len(servers_after)} servers")

# 5. Caller owns cleanup
print("5. Closing connection...")
conn.close()
print("Done!")
