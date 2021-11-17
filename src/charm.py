#!/usr/bin/env python3

# Copyright 2021 Canonical Ltd
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

from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus

from pylspci.parsers import SimpleParser
from ruamel.yaml import YAML


class NovaComputeNvidiaVgpuCharm(CharmBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    def _on_install(self, _):
        """install hook."""
        self.__set_ready_unit_status()

    def _on_upgrade_charm(self, _):
        """upgrade-charm hook."""
        self.__set_ready_unit_status()

    def _on_config_changed(self, _):
        """config-changed hook."""
        if not self._has_nvidia_gpu_hardware():
            return

        vgpu_device_mappings_str = self.config.get('vgpu-device-mappings')
        if vgpu_device_mappings_str is not None:
            vgpu_device_mappings = YAML().load(vgpu_device_mappings_str)
            logging.debug('vgpu-device-mappings={}'.format(
                vgpu_device_mappings))

    def __set_ready_unit_status(self):
        """Set the unit status to active/ready."""
        unit_status_msg = (
            'Unit is ready: '
            + ('no ' if not self._has_nvidia_gpu_hardware() else '')
            + 'NVIDIA GPU found')
        self.unit.status = ActiveStatus(unit_status_msg)

    @staticmethod
    def _has_nvidia_gpu_hardware():
        """Search for NVIDIA GPU hardware.

        :returns: True if some NVIDIA GPU hardware is found on the current
                  unit.
        :rtype: bool
        """
        nvidia_gpu_hardware_found = False
        for device in SimpleParser().run():
            device_class = device.cls.name
            device_vendor = device.vendor.name
            device_subsystem_vendor = device.subsystem_vendor.name
            if '3D' in device_class and ('NVIDIA' in device_vendor or
                                         'NVIDIA' in device_subsystem_vendor):
                logging.debug('NVIDIA GPU found: {}'.format(device))
                # NOTE(lourot): we could `break` out here but it's interesting
                # for debugging purposes to print them all.
                nvidia_gpu_hardware_found = True

        if not nvidia_gpu_hardware_found:
            logging.debug('No NVIDIA GPU found.')

        return nvidia_gpu_hardware_found


if __name__ == '__main__':
    main(NovaComputeNvidiaVgpuCharm)
