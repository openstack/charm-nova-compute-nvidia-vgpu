#!/bin/bash -e
# Ensure all SRIOV devices have been setup
/usr/lib/nvidia/sriov-manage -e srvio-manage -e ALL
# Now go through all domains and initialise any used mdevs
/opt/remediate-nova-mdevs

