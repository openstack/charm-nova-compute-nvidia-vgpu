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

from ruamel.yaml import YAML


class NovaComputeNvidiaVgpuCharm(CharmBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    def _on_install(self, _):
        """install hook."""
        self.unit.status = ActiveStatus('Unit is ready')

    def _on_config_changed(self, _):
        """config-changed hook."""
        vgpu_device_mappings_str = self.config.get('vgpu-device-mappings')
        if vgpu_device_mappings_str is not None:
            vgpu_device_mappings = YAML().load(vgpu_device_mappings_str)
            logging.debug('vgpu-device-mappings={}'.format(
                vgpu_device_mappings))


if __name__ == '__main__':
    main(NovaComputeNvidiaVgpuCharm)
