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
import json

from charmhelpers.core.hookenv import cached
from charmhelpers.core.host import file_hash
from charmhelpers.fetch import (
    apt_cache,
    apt_install,
)

import ops_openstack.plugins.classes

from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    ModelError,
)

from pylspci.parsers import SimpleParser
from ruamel.yaml import YAML


class NovaComputeNvidiaVgpuCharm(ops_openstack.core.OSBaseCharm):

    # NOTE(lourot): as of today (2021-11-25), OSBaseCharm doesn't make use of
    # this dict's keys (config files) but only uses its values (service names):
    RESTART_MAP = {
        '/usr/share/nvidia/vgpu/vgpuConfig.xml': ['nvidia-vgpu-mgr'],
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        super().register_status_check(self._check_status)

        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.nova_vgpu_relation_joined,
                               self._on_nova_vgpu_relation_joined_or_changed)
        self.framework.observe(self.on.nova_vgpu_relation_changed,
                               self._on_nova_vgpu_relation_joined_or_changed)

        # hash of the last successfully installed NVIDIA vGPU software passed
        # as resource to the charm:
        self._stored.set_default(last_installed_resource_hash=None)

    def _on_config_changed(self, _):
        """config-changed hook."""
        # NOTE(lourot): We want to re-install the software here if a new
        # version has just been provided as a charm resource.
        self._install_nvidia_software_if_needed()

        for relation in self.framework.model.relations.get('nova-vgpu'):
            self._set_principal_unit_relation_data(relation.data[self.unit])

        self.update_status()

    def _on_start(self, _):
        """start hook."""
        # NOTE(lourot): We install software in the `start` hook instead of
        # the `install` hook because we want to be able to install software
        # after a reboot if NVIDIA hardware has then been added for the
        # first time.
        self._install_nvidia_software_if_needed()

        # NOTE(lourot): this is used by OSBaseCharm.update_status():
        self._stored.is_started = True

        self.update_status()

    def _on_nova_vgpu_relation_joined_or_changed(self, event):
        self._set_principal_unit_relation_data(
            event.relation.data[self.unit])

    def services(self):
        # If no NVIDIA software is expected to be installed on this particular
        # unit, then no service should be expected to run by
        # OSBaseCharm.update_status(). Otherwise the services from the
        # RESTART_MAP are expected to run.
        if not self._is_nvidia_software_to_be_installed():
            return []
        return super().services()

    def _check_status(self):
        """Determine the unit status to be set.

        :rtype: StatusBase
        """
        unit_status_msg = ('no ' if not self._has_nvidia_gpu_hardware()
                           else '') + 'NVIDIA GPU found; '

        installed_versions = self._installed_nvidia_software_versions()
        if len(installed_versions) > 0:
            unit_status_msg += 'installed NVIDIA software: '
            unit_status_msg += ', '.join(installed_versions)
        else:
            unit_status_msg += 'no NVIDIA software installed'

        if self._is_nvidia_software_to_be_installed() and len(
                installed_versions) == 0:
            return BlockedStatus(unit_status_msg)

        return ActiveStatus('Unit is ready: ' + unit_status_msg)

    def _set_principal_unit_relation_data(self, principal_unit_relation_data):
        """Pass configuration to a principal unit."""
        vgpu_device_mappings_str = self.config.get('vgpu-device-mappings')
        if vgpu_device_mappings_str is not None:
            vgpu_device_mappings = YAML().load(vgpu_device_mappings_str)
            logging.debug('vgpu-device-mappings={}'.format(
                vgpu_device_mappings))

            nova_conf = json.dumps({
                'nova': {
                    '/etc/nova/nova.conf': {
                        'sections': {
                            'DEFAULT': [
                                # NOTE(lourot): will be implemented in next
                                # iteration:
                                # ('key', 'value')
                            ]
                        }
                    }
                }
            })
            _set_relation_data(
                principal_unit_relation_data, 'subordinate_configuration',
                nova_conf)
            logging.debug(
                'relation data to principal unit set to '
                'subordinate_configuration={}'.format(nova_conf))

    def _install_nvidia_software_if_needed(self):
        """Install the NVIDIA software on this unit if relevant."""
        if self._is_nvidia_software_to_be_installed():
            nvidia_software_path, nvidia_software_hash = (
                self._path_and_hash_nvidia_resource())

            if nvidia_software_path is None:
                # No software has been provided as charm resource. We can't
                # install anything. OSBaseCharm.update_status() will be
                # executed later and put the unit in blocked state.
                return

            last_installed_hash = self._stored.last_installed_resource_hash
            if nvidia_software_hash == last_installed_hash:
                logging.info(
                    'NVIDIA vGPU software with hash {} already installed, '
                    'skipping'.format(nvidia_software_hash))
                return

            logging.info(
                'Installing NVIDIA vGPU software with hash {}'.format(
                    nvidia_software_hash))
            apt_install([nvidia_software_path], fatal=True)
            self._stored.last_installed_resource_hash = nvidia_software_hash

    @cached
    def _is_nvidia_software_to_be_installed(self):
        """Determine whether the NVIDIA vGPU software is to be installed.

        :returns: True if the software is to be installed and set up on the
                  unit.
        :rtype: bool
        """
        return (self._has_nvidia_gpu_hardware() or
                self.config.get('force-install-nvidia-vgpu'))

    def _path_and_hash_nvidia_resource(self):
        """Get path to and hash of software provided as charm resource.

        :returns: Pair of path and hash. (None, None) if no charm resource has
                  been provided.
        :rtype: Tuple[PosixPath, str]
        """
        try:
            nvidia_vgpu_software_path = (
                self.framework.model.resources.fetch('nvidia-vgpu-software'))
        except ModelError:
            return None, None

        return nvidia_vgpu_software_path, file_hash(nvidia_vgpu_software_path)

    def _installed_nvidia_software_versions(self):
        """Get a list of installed NVIDIA vGPU software versions.

        :returns: List of versions
        :rtype: List[str]
        """
        return [package['version'] for package in
                apt_cache().dpkg_list(['nvidia-vgpu-ubuntu-*']).values()]

    @classmethod
    @cached
    def _has_nvidia_gpu_hardware(cls):
        """Search for NVIDIA GPU hardware.

        :returns: True if some NVIDIA GPU hardware is found on the current
                  unit.
        :rtype: bool
        """
        return cls._has_nvidia_gpu_hardware_notcached()

    @staticmethod
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


def _set_relation_data(relation_data, key, value):
    """Mockable setter.

    Workaround for https://github.com/canonical/operator/issues/703
    Used in unit test
    TestNovaComputeNvidiaVgpuCharm.test_nova_vgpu_relation_joined
    """
    relation_data[key] = value


if __name__ == '__main__':
    main(NovaComputeNvidiaVgpuCharm)
