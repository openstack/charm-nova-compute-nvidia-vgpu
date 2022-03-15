# Overview

This subordinate charm provides the Nvidia vGPU support to the
[OpenStack Nova Compute service][charm-nova-compute].

# Usage

## Deployment

We are assuming a pre-existing OpenStack deployment (Queens or newer).

Deploy nova-compute-nvidia-vgpu as a subordinate to the nova-compute charm:

    juju deploy ch:nova-compute-nvidia-vgpu --channel=yoga/edge
    juju add-relation nova-compute-nvidia-vgpu:nova-vgpu nova-compute:nova-vgpu

Pass the [proprietary NVIDIA software package][nvidia-software] (`510.47.03` or
newer) as a resource to the charm:

    juju attach nova-compute-nvidia-vgpu \
        nvidia-vgpu-software=./nvidia-vgpu-ubuntu-510_510.47.03_amd64.deb

Once the model settles, reboot the corresponding compute nodes:

    juju run -a nova-compute-nvidia-vgpu -- sudo reboot

### vGPU type definition

Each compute node has one or several physical GPUs. Each physical GPU can then
be divided into one or several [virtual GPUs][virtual-gpu] of a given type.
Virtual GPUs will later be claimed by and exposed to guests upon guest
creation. Start by listing the available vGPU types for each physical GPU:

    juju run-action nova-compute-nvidia-vgpu/0 list-vgpu-types --wait
    [...]
        nvidia-256, 0000:41:00.0, GRID RTX6000-1Q, num_heads=4, frl_config=60, framebuffer=1024M, max_resolution=5120x2880, max_instance=24
        nvidia-257, 0000:41:00.0, GRID RTX6000-2Q, num_heads=4, frl_config=60, framebuffer=2048M, max_resolution=7680x4320, max_instance=12
        nvidia-258, 0000:41:00.0, GRID RTX6000-3Q, num_heads=4, frl_config=60, framebuffer=3072M, max_resolution=7680x4320, max_instance=8
        nvidia-259, 0000:41:00.0, GRID RTX6000-4Q, num_heads=4, frl_config=60, framebuffer=4096M, max_resolution=7680x4320, max_instance=6
    [...]
        nvidia-105, 0000:c1:00.0, GRID V100-1Q, num_heads=4, frl_config=60, framebuffer=1024M, max_resolution=5120x2880, max_instance=16
        nvidia-106, 0000:c1:00.0, GRID V100-2Q, num_heads=4, frl_config=60, framebuffer=2048M, max_resolution=7680x4320, max_instance=8
        nvidia-107, 0000:c1:00.0, GRID V100-4Q, num_heads=4, frl_config=60, framebuffer=4096M, max_resolution=7680x4320, max_instance=4
        nvidia-108, 0000:c1:00.0, GRID V100-8Q, num_heads=4, frl_config=60, framebuffer=8192M, max_resolution=7680x4320, max_instance=2
    [...]

As we can see, `nova-compute-nvidia-vgpu/0` has two physical GPUs:
`0000:41:00.0` and `0000:c1:00.0`. By selecting the vGPU type `nvidia-108` on
`0000:c1:00.0`, two vGPUs will be available for future guests:

    juju config nova-compute-nvidia-vgpu vgpu-device-mappings="{'nvidia-108': ['0000:c1:00.0']}"

> **NOTE**: on releases older than Stein, only one vGPU type can be selected
> accross all available physical GPUs. Starting from Stein each physical GPU
> can be assigned a different vGPU type.

On OpenStack Stein and newer, once the model has settled, these vGPUs can be
listed via the OpenStack CLI:

    openstack resource provider list
    +--------------------------------------+-----------------------------------+------------+--------------------------------------+--------------------------------------+
    | uuid                                 | name                              | generation | root_provider_uuid                   | parent_provider_uuid                 |
    +--------------------------------------+-----------------------------------+------------+--------------------------------------+--------------------------------------+
    | 0883c2b5-bad2-4abc-a179-e33344361475 | node-sparky.maas                  |          2 | 0883c2b5-bad2-4abc-a179-e33344361475 | None                                 |
    | 4b0dbc58-0c85-4a80-8dd6-d43d1bd6ec53 | node-sparky.maas_pci_0000_c1_00_0 |          1 | 0883c2b5-bad2-4abc-a179-e33344361475 | 0883c2b5-bad2-4abc-a179-e33344361475 |
    +--------------------------------------+-----------------------------------+------------+--------------------------------------+--------------------------------------+

    openstack resource provider inventory list 4b0dbc58-0c85-4a80-8dd6-d43d1bd6ec53
    +----------------+------------------+----------+----------+----------+-----------+-------+------+
    | resource_class | allocation_ratio | min_unit | max_unit | reserved | step_size | total | used |
    +----------------+------------------+----------+----------+----------+-----------+-------+------+
    | VGPU           |              1.0 |        1 |        2 |        0 |         1 |     2 |    0 |
    +----------------+------------------+----------+----------+----------+-----------+-------+------+

### Nova flavor definition

In order to expose a vGPU of the type defined earlier to any guest created with
the `m1.small` flavor, create a new trait and assign it to the flavor:

    openstack --os-placement-api-version 1.6 trait create CUSTOM_NVIDIA_108
    openstack --os-placement-api-version 1.6 resource provider trait set --trait CUSTOM_NVIDIA_108 4b0dbc58-0c85-4a80-8dd6-d43d1bd6ec53
    openstack flavor set m1.small --property resources:VGPU=1 --property trait:CUSTOM_NVIDIA_108=required

> **NOTE**: on releases older than Stein, since there is only one vGPU type
> and it doesn't show up in the resource provider list, no trait can be
> created. The flavor can simply be modified with
> `openstack flavor set m1.small --property resources:VGPU=1`

After creating an instance of this flavor, the resource provider inventory
list will show one vGPU being used:

    openstack server create --flavor m1.small ...
    openstack resource provider inventory list 4b0dbc58-0c85-4a80-8dd6-d43d1bd6ec53
    +----------------+------------------+----------+----------+----------+-----------+-------+------+
    | resource_class | allocation_ratio | min_unit | max_unit | reserved | step_size | total | used |
    +----------------+------------------+----------+----------+----------+-----------+-------+------+
    | VGPU           |              1.0 |        1 |        2 |        0 |         1 |     2 |    1 |
    +----------------+------------------+----------+----------+----------+-----------+-------+------+

# Bugs

Please report bugs on [Launchpad][lp-bugs-nova-nvidia].

For general questions please refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[charm-nova-compute]: https://jaas.ai/nova-compute
[cg]: https://docs.openstack.org/charm-guide
[lp-bugs-nova-nvidia]: https://bugs.launchpad.net/charm-nova-compute-nvidia-vgpu/+filebug
[nvidia-software]: https://docs.nvidia.com/grid/index.html
[virtual-gpu]: https://docs.openstack.org/nova/ussuri/admin/virtual-gpu.html
