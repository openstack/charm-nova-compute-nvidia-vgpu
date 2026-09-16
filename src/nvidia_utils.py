#!/usr/bin/env python3

# Copyright 2022 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import logging
import os
from pathlib import Path

from charmhelpers.core.hookenv import cached
from charmhelpers.core.kernel import update_initramfs
from charmhelpers.core.templating import render
from charmhelpers.fetch import (
    apt_cache,
)

from pylspci.parsers import SimpleParser

import vfio_utils


def installed_nvidia_software_versions():
    """Get a list of installed NVIDIA vGPU software versions.

    :returns: List of versions
    :rtype: List[str]
    """
    return [package['version'] for package in
            _installed_nvidia_software_packages()]


def installed_nvidia_software_package_names():
    """Get a list of installed NVIDIA vGPU software package names.

    :returns: List of versions
    :rtype: List[str]
    """
    return [package['name'] for package in
            _installed_nvidia_software_packages()]


def disable_nouveau_driver():
    """Disable the nouveau driver.

    This driver prevents the nvidia-vgpu-mgr service from starting. A reboot is
    required.
    """
    config_file = '/etc/modprobe.d/disable-nouveau.conf'
    render(os.path.basename(config_file), config_file, {})
    update_initramfs()


GPU_DEVICE_CLASS = '0x030200'


def list_vgpu_types():
    """Human-readable list of all vGPU types registered by the NVIDIA driver.

    :rtype: str
    """
    # NOTE(lourot): we are reinventing `mdevctl types` here. Unfortunately
    # `mdevctl` is not available on Bionic.

    vgpu_types_dirname = 'mdev_supported_types'
    found_pci_addr_dirs = []
    skipped_non_gpu_dirs = []
    for root, dirs, files in os.walk('/sys/devices'):
        if vgpu_types_dirname in dirs:
            # Other types of device can present mediated devices
            # so ensure that only 3D controller class devices
            # are listed
            device_class = (
                Path(os.path.join(root, 'class')).read_text().rstrip()
            )
            if device_class != GPU_DEVICE_CLASS:
                skipped_non_gpu_dirs.append(
                    '{} (class={})'.format(root, device_class))
                continue
            # At this point root looks like
            # /sys/devices/pci0000:40/0000:40:03.1/0000:41:00.0
            found_pci_addr_dirs.append(root)

    logging.debug(
        'list_vgpu_types: detected vGPU mode=%s',
        detect_vgpu_mode())
    logging.debug(
        'list_vgpu_types: found %d mdev_supported_types dir(s): %s',
        len(found_pci_addr_dirs), found_pci_addr_dirs)
    if skipped_non_gpu_dirs:
        logging.debug(
            'list_vgpu_types: skipped %d non-3D-controller dir(s): %s',
            len(skipped_non_gpu_dirs), skipped_non_gpu_dirs)

    output_lines = []
    for pci_addr_dir in found_pci_addr_dirs:
        root = os.path.join(pci_addr_dir, vgpu_types_dirname)
        for vgpu_type in sorted(os.listdir(root)):
            output_line = vgpu_type
            output_line += ', ' + os.path.basename(pci_addr_dir)
            output_line += (
                ', ' + Path(os.path.join(root, vgpu_type, 'name')
                            ).read_text().rstrip())
            output_line += (
                ', ' + Path(os.path.join(root, vgpu_type, 'description')
                            ).read_text().rstrip())

            # At this point output_line looks like
            # nvidia-256, 0000:41:00.0, GRID RTX6000-1Q, num_heads=4,
            #   frl_config=60, framebuffer=1024M, max_resolution=5120x2880,
            #   max_instance=24
            output_lines.append(output_line)

    return '\n'.join(output_lines)


@cached
def has_nvidia_gpu_hardware():
    """Search for NVIDIA GPU hardware.

    :returns: a tuple of (bool, int) indicating if NVIDIA GPU hardware
              is found and how many GPU's where detected.
    :rtype: (bool, int)
    """
    return _has_nvidia_gpu_hardware_notcached()


def _has_nvidia_gpu_hardware_notcached():
    num_nvidia_devices = len(_find_nvidia_gpu_pci_addresses())
    if num_nvidia_devices == 0:
        logging.debug('No NVIDIA GPU found.')

    return num_nvidia_devices > 0, num_nvidia_devices


def _installed_nvidia_software_packages():
    """Get a list of installed NVIDIA vGPU software packages.

    :returns: List of packages
    :rtype: List[Dict[str, str]]
    """
    return apt_cache().dpkg_list(['nvidia-vgpu-ubuntu-*']).values()


def _find_nvidia_gpu_pci_addresses():
    """Find PCI addresses of NVIDIA GPU physical functions.

    SR-IOV virtual functions are also listed by lspci as NVIDIA 3D
    controllers; they are skipped so that callers only see physical GPUs.

    :returns: List of PCI addresses (e.g. ['0000:41:00.0'])
    :rtype: List[str]
    """
    addresses = []
    for device in SimpleParser().run():
        if '3D' in device.cls.name and (
                'NVIDIA' in device.vendor.name or
                'NVIDIA' in getattr(device.subsystem_vendor, 'name', '')):
            slot = device.slot
            pci_address = '{:04x}:{:02x}:{:02x}.{:x}'.format(
                slot.domain or 0,
                slot.bus,
                slot.device,
                slot.function)
            if vfio_utils.is_virtual_function(pci_address):
                continue
            logging.debug('Found NVIDIA GPU at PCI address {}'.format(
                pci_address))
            addresses.append(pci_address)
    logging.debug('Found {} NVIDIA GPU(s): {}'.format(
        len(addresses), addresses))
    return addresses


def detect_vgpu_mode():
    """Detect the vGPU mode from sysfs.

    Each physical GPU and its VFs are inspected: on SR-IOV GPUs both the
    vendor-specific VFIO ``nvidia/`` directory and the mdev
    ``mdev_supported_types`` directory live on the VFs. VFIO takes priority
    when both are found (single mode per host).

    :returns: 'vfio', 'mdev', or 'none'
    :rtype: str
    """
    vfio_detected = False
    mdev_detected = False

    for pf_address in _find_nvidia_gpu_pci_addresses():
        addresses = [pf_address] + [
            vf['pci_address'] for vf in vfio_utils.list_vfs(pf_address)]
        for address in addresses:
            vfio_detected |= vfio_utils.has_vendor_vfio_dir(address)
            mdev_detected |= vfio_utils.has_mdev_supported_types(address)

    if vfio_detected:
        return 'vfio'
    if mdev_detected:
        return 'mdev'
    return 'none'


def detect_vgpu_mode_with_override(config_mode):
    """Detect vGPU mode, respecting operator override.

    :param config_mode: 'auto', 'mdev', or 'vfio'
    :type config_mode: str
    :returns: 'vfio', 'mdev', or 'none'
    :rtype: str
    """
    if config_mode == 'auto':
        return detect_vgpu_mode()
    return config_mode


def list_vgpu_types_vfio():
    """Human-readable list of all vGPU types in VFIO mode.

    :rtype: str
    """
    catalog = vfio_utils.read_catalog()
    output_lines = []
    for pf_address in _find_nvidia_gpu_pci_addresses():
        for vf in vfio_utils.list_vfs(pf_address):
            vf_address = vf['pci_address']
            current = vfio_utils.read_current_vgpu_type(vf_address)
            if current:
                output_lines.append('{}, {}, {} (assigned)'.format(
                    current, vf_address,
                    catalog.get(pf_address, {}).get(current, 'unknown')))
            for vgpu_type in vfio_utils.read_creatable_vgpu_types(vf_address):
                output_lines.append('{}, {}, {}'.format(
                    vgpu_type['id'], vf_address, vgpu_type['name']))
    return '\n'.join(output_lines)
