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
      results:
        vgpu-types-count: "1472"
        result: |-
          1472 vGPU type(s) found
          see 'juju ssh -m <model> nova-compute-nvidia-vgpu/0 cat <charm-dir>/vgpu-types-results/vgpu-types-XXXXXX.txt' for the full list

The full listing can be very large on hosts with many GPUs (for example SR-IOV
vGPU with dozens of virtual functions), so instead of returning it inline the
action writes it to a file under the charm's base directory on the unit and the
`result` field tells you how to read it. This keeps the results within the
operating system's argument-size limit. Run the exact `juju ssh ... cat`
command from `result` to print the full list:

    juju ssh -m <model> nova-compute-nvidia-vgpu/0 cat <charm-dir>/vgpu-types-results/vgpu-types-XXXXXX.txt
    nvidia-256, 0000:41:00.0, GRID RTX6000-1Q, num_heads=4, frl_config=60, framebuffer=1024M, max_resolution=5120x2880, max_instance=24
    nvidia-257, 0000:41:00.0, GRID RTX6000-2Q, num_heads=4, frl_config=60, framebuffer=2048M, max_resolution=7680x4320, max_instance=12
    [...]
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

## NVIDIA software version requirements

The vGPU mode supported by a compute node depends on the NVIDIA vGPU software
version and the Ubuntu series:

| NVIDIA vGPU software | Driver branch | vGPU mode | Ubuntu series |
|----------------------|---------------|-----------|---------------|
| vGPU 16.x            | R525          | mdev      | 22.04, 24.04  |
| vGPU 17.x and newer  | R550+         | VFIO      | 24.04 (SR-IOV capable GPUs) |
| vGPU 20.x            | R595          | VFIO      | 24.04, 26.04  |

## vGPU mode configuration

The `vgpu-mode` config option selects how vGPUs are exposed to guests:

* `auto` (default): the charm detects the supported mode by checking sysfs.
  The VFIO path is checked first, then the mdev path.
* `mdev`: force mediated device (mdev) mode.
* `vfio`: force VFIO (SR-IOV) mode.

## VFIO mode configuration

In VFIO mode, the `gpu-profiles` config option declares which vGPU profile to
create on each physical GPU, and how many:

    gpu-profiles: |
      {"0000:41:00.0": [{"type": "A40-4Q", "count": 12}],
       "0000:81:00.0": [{"type": "A40-2Q", "count": 4, "policy": "fail"}]}

Profile names are the ones listed by the `list-vgpu-types` action, as found in
the VFs' `creatable_vgpu_types` sysfs file without the `NVIDIA ` prefix
(`NVIDIA A40-4Q` and `A40-4Q` are both accepted). Each VF holds one vGPU, so
`count` is the number of VFs that get the profile.

Each profile entry accepts an optional `policy` field:

* `ignore` (default): if the requested profile cannot be created (unknown
  type, no free VF left or not enough frame buffer on the GPU), the charm
  creates as many vGPUs as possible and logs a warning.
* `fail`: the unit goes into a blocked state if the profile cannot be
  created, including when the GPU's VFs are not visible or its vGPU types
  are unknown (a reboot may be required to enable the VFs).

Malformed `gpu-profiles` values also put the unit in a blocked state.

Profiles are assigned deterministically: VFs keep the profile they already
have while it is still requested, and the remaining vGPUs are assigned in
`virtfn` order. The result is stored in `/etc/nvidia-vgpu-state.yaml`.

Reducing the `count` leaves the surplus VFs untouched: their vGPUs keep
running until the next reboot, when only the requested number is recreated.

### One vGPU type per GPU (Nova limitation)

With PCI tracking in Placement, Nova reports all the VFs of a GPU as a single
resource provider with a single resource class, and nova-compute refuses to
start when VFs of the same GPU are configured with different resource classes
(`PlacementPciMixedResourceClassException`). Since every vGPU type is exposed
as its own `CUSTOM_NVIDIA_<profile id>` resource class, each GPU can only
provide one vGPU type, including types of the same size from different series
such as `A40-2B` and `A40-2Q`. Listing more than one profile for a GPU in
`gpu-profiles` blocks the unit.

To offer several vGPU types on a compute node, give each GPU a different
profile.

For the same reason, mixing frame buffer sizes on a GPU (NVIDIA vGPU
heterogeneous mode) is not used: the boot-time setup makes sure heterogeneous
mode is disabled on every SR-IOV GPU, so GPUs run in equal-size mode, which
allows the largest number of vGPUs per type. If heterogeneous mode was enabled
by hand, the unit reports `vGPU heterogeneous mode enabled on <gpu>, manual
reboot required to disable it`.

### Profiles are applied at boot

The charm never changes the vGPU type of a VF at runtime, since a VF may be in
use by an instance. Profiles are applied by the `nvidia-vgpu-host-setup`
service at boot, before `nova-compute` starts. Until then the unit reports
`N vGPU profile change(s) pending, manual reboot required`, and only VFs
that already carry their desired profile are passed to Nova.

On a new compute node, two reboots are needed: the first one loads the NVIDIA
driver and enables the VFs so the charm can resolve `gpu-profiles`; the
second one applies the profiles.

If the boot-time setup fails, the unit is blocked with the first error; the
full log is available with `journalctl -u nvidia-vgpu-host-setup`.

### Flavors

Each profile in use is exposed as a PCI alias named after the profile and
backed by the `CUSTOM_NVIDIA_<profile id>` resource class:

    openstack flavor set vgpu.a40-4q --property "pci_passthrough:alias"="A40-4Q:1"

The nova-cloud-controller charm does not receive these aliases from this charm
yet. Configure the same aliases on nova-cloud-controller with its `pci-alias`
option, e.g.:

    juju config nova-cloud-controller pci-alias='[{"device_type": "type-VF",
      "resource_class": "CUSTOM_NVIDIA_560", "name": "A40-4Q"}]'

## PCI-in-Placement requirement

VFIO mode relies on Nova's PCI-in-Placement support. It requires:

* `[pci]report_in_placement=True` on nova-compute, set with
  `juju config nova-compute pci-report-in-placement=true`.
* `[filter_scheduler]pci_in_placement=True` on nova-cloud-controller, set
  with `juju config nova-cloud-controller pci-in-placement=true`.

The unit is blocked until `report_in_placement` is enabled in nova.conf.

## Placement performance tuning

Placement performance options must be configured on the Placement charm
directly, rather than on this nova-compute subordinate. For large PCI provider
trees, configure Placement's allocation-candidate generation strategy and
`optimize_for_wide_provider_trees` option on the Placement application.

## Scheduler coexistence

Jammy (mdev) and Noble (VFIO) compute nodes can coexist in the same cloud.
mdev computes advertise the `VGPU` resource class, while VFIO computes
advertise per-profile `CUSTOM_NVIDIA_*` resource classes, so the scheduler can
target each pool independently.

## Nova requirement for Caracal

VFIO mode needs Nova's PCI `managed` tag, available from Epoxy. On Caracal it
needs `nova-common` `3:29.2.0-0ubuntu1.8+vgpu1` or later, which carries the
backport; with an older version the charm logs a warning but does not block
the unit. The
`ppa:canonical-openstack/charmed-openstack-noble-caracal` PPA provides patched
nova-compute and placement packages with the backports required for VFIO mode.
Other releases before Epoxy are not supported in VFIO mode.

## Live migration limitation

VFIO-backed instances cannot be live-migrated on Caracal. Cold migration
remains available.

# Bugs

Please report bugs on [Launchpad][lp-bugs-nova-nvidia].

For general questions please refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[charm-nova-compute]: https://jaas.ai/nova-compute
[cg]: https://docs.openstack.org/charm-guide
[lp-bugs-nova-nvidia]: https://bugs.launchpad.net/charm-nova-compute-nvidia-vgpu/+filebug
[nvidia-software]: https://docs.nvidia.com/grid/index.html
[virtual-gpu]: https://docs.openstack.org/nova/ussuri/admin/virtual-gpu.html
