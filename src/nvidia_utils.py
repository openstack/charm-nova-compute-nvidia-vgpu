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

from charmhelpers.core.hookenv import cached
from charmhelpers.core.kernel import update_initramfs
from charmhelpers.core.templating import render
from charmhelpers.fetch import (
    apt_cache,
)

from pylspci.parsers import SimpleParser


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


@cached
def has_nvidia_gpu_hardware():
    """Search for NVIDIA GPU hardware.

    :returns: True if some NVIDIA GPU hardware is found on the current
              unit.
    :rtype: bool
    """
    return _has_nvidia_gpu_hardware_notcached()


def _has_nvidia_gpu_hardware_notcached():
    nvidia_gpu_hardware_found = False
    for device in SimpleParser().run():
        device_class = device.cls.name
        device_vendor = device.vendor.name
        try:
            device_subsystem_vendor = device.subsystem_vendor.name
        except AttributeError:
            device_subsystem_vendor = ''

        if '3D' in device_class and ('NVIDIA' in device_vendor or
                                     'NVIDIA' in device_subsystem_vendor):
            logging.debug('NVIDIA GPU found: {}'.format(device))
            # NOTE(lourot): we could `break` out here but it's interesting
            # for debugging purposes to print them all.
            nvidia_gpu_hardware_found = True

    if not nvidia_gpu_hardware_found:
        logging.debug('No NVIDIA GPU found.')

    return nvidia_gpu_hardware_found


def _installed_nvidia_software_packages():
    """Get a list of installed NVIDIA vGPU software packages.

    :returns: List of packages
    :rtype: List[Dict[str, str]]
    """
    return apt_cache().dpkg_list(['nvidia-vgpu-ubuntu-*']).values()
