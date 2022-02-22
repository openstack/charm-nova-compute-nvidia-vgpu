# Overview

This directory provides Zaza test definitions and bundles to verify basic
deployment functionality from the perspective of this charm, its requirements
and its features, as exercised in a subset of the full OpenStack deployment
test bundle topology.

Run the smoke tests with:

```bash
cd ../
tox -e build
tox -e func-smoke
```

For full details on functional testing of OpenStack charms please refer to
the [functional testing](https://docs.openstack.org/charm-guide/latest/reference/testing.html#functional-testing)
section of the OpenStack Charm Guide.
