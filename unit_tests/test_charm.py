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

import sys
import unittest

from mock import ANY, patch

from ops.model import ActiveStatus
from ops.testing import Harness

sys.path.append('src')  # noqa

import charm


class CharmTestCase(unittest.TestCase):

    def setUp(self, obj, patches):
        super().setUp()
        self.patches = patches
        self.obj = obj
        self.patch_all()

    def patch(self, method):
        _m = patch.object(self.obj, method)
        mock = _m.start()
        self.addCleanup(_m.stop)
        return mock

    def patch_all(self):
        for method in self.patches:
            setattr(self, method, self.patch(method))


class TestNovaComputeNvidiaVgpuCharm(CharmTestCase):

    _PATCHES = [
        'check_status',
        'install_nvidia_software_if_needed',
        'is_nvidia_software_to_be_installed',
        'set_relation_data',
    ]

    def setUp(self):
        super().setUp(charm, self._PATCHES)
        self.harness = Harness(charm.NovaComputeNvidiaVgpuCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_init(self):
        self.assertEqual(
            self.harness.framework.model.app.name,
            'nova-compute-nvidia-vgpu')
        self.assertFalse(self.harness.charm._stored.is_started)
        self.assertIsNone(
            self.harness.charm._stored.last_installed_resource_hash)

    def test_nova_vgpu_relation_joined(self):
        # NOTE(lourot): these functions get called by the update-status hook,
        # which is irrelevant for this test:
        self.check_status.return_value = ActiveStatus('Unit is ready')
        self.is_nvidia_software_to_be_installed.return_value = False

        self.harness.set_leader(True)
        self.harness.update_config({
            "vgpu-device-mappings": "{'vgpu_type1': ['device_address1']}"
        })
        relation_id = self.harness.add_relation('nova-vgpu', 'nova-compute')
        self.harness.add_relation_unit(relation_id, 'nova-compute/0')

        # Verify that nova-compute-vgpu-charm sets relation data to its
        # principle nova-compute.
        # NOTE(lourot): We mock _set_relation_data() instead of using
        # self.harness.get_relation_data() as a workaround for
        # https://github.com/canonical/operator/issues/703
        self.set_relation_data.assert_called_once_with(
            ANY, 'subordinate_configuration', ANY)
