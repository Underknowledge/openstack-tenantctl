# Examples

This directory contains example code demonstrating how to use TenantCtl programmatically.

## Connection Reuse Example

**File:** `example_connection_reuse.py`

Demonstrates how to authenticate to OpenStack once and reuse the connection for both:
- Custom OpenStack SDK operations (listing servers, networks, etc.)
- TenantCtl provisioning operations

This pattern is useful when you need to integrate TenantCtl into a larger application that performs multiple OpenStack operations.

**Usage:**
```bash
python examples/example_connection_reuse.py
```

**Key concepts:**
- Authenticate once with `openstack.connect()`
- Pass the connection to `TenantCtl.run()` via the `connection` parameter
- TenantCtl will use the connection but NOT close it
- Caller is responsible for closing the connection
