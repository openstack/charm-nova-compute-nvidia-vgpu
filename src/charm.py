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


import ops_openstack.plugins.classes

from ops.main import main

from charm_utils import (
    check_status,
    install_nvidia_software_if_needed,
    is_nvidia_software_to_be_installed,
    set_principal_unit_relation_data,
)


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
        install_nvidia_software_if_needed(self._stored, self.config,
                                          self.framework.model.resources)

        for relation in self.framework.model.relations.get('nova-vgpu'):
            set_principal_unit_relation_data(relation.data[self.unit],
                                             self.config)

        self.update_status()

    def _on_start(self, _):
        """start hook."""
        # NOTE(lourot): We install software in the `start` hook instead of
        # the `install` hook because we want to be able to install software
        # after a reboot if NVIDIA hardware has then been added for the
        # first time.
        install_nvidia_software_if_needed(self._stored, self.config,
                                          self.framework.model.resources)

        # NOTE(lourot): this is used by OSBaseCharm.update_status():
        self._stored.is_started = True

        self.update_status()

    def _on_nova_vgpu_relation_joined_or_changed(self, event):
        set_principal_unit_relation_data(event.relation.data[self.unit],
                                         self.config)

    def services(self):
        # If no NVIDIA software is expected to be installed on this particular
        # unit, then no service should be expected to run by
        # OSBaseCharm.update_status(). Otherwise the services from the
        # RESTART_MAP are expected to run.
        if not is_nvidia_software_to_be_installed(self.config):
            return []
        return super().services()

    def _check_status(self):
        """Determine the unit status to be set.

        :rtype: ops.model.StatusBase
        """
        return check_status(self.config)


if __name__ == '__main__':
    main(NovaComputeNvidiaVgpuCharm)
