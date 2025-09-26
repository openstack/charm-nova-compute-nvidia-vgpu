#!/bin/bash -e
# Ensure all SRIOV devices have been setup
sleep 20
/usr/lib/nvidia/sriov-manage -e ALL
# Ensure mdev devices are registered before continuing
max=10
while true; do
    if ! $(/usr/lib/nvidia/sriov-manage -e  $(nvidia-smi -q | grep ^GPU| cut -d ' ' -f2-)| grep -q "already has VFs enabled."); then
        echo "Waiting for GPU nvidia mdev registration"
        sleep 1
        ((max--)) && continue
    fi
    break
done
# Now go through all domains and initialise any used mdevs
/opt/remediate-nova-mdevs
