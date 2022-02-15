# Copyright 2016 Canonical Ltd
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

import unittest

from mock import ANY, MagicMock, patch

from ops.model import ActiveStatus
from ops.testing import Harness

import src.charm


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


class MockLspciProperty:
    def __init__(self, name):
        self.name = name


class MockLspciDevice:
    def __init__(self, cls_name, vendor_name):
        self.cls = MockLspciProperty(cls_name)
        self.vendor = MockLspciProperty(vendor_name)


class TestNovaComputeNvidiaVgpuCharm(CharmTestCase):

    _PATCHES = [
        'SimpleParser',
        '_set_relation_data',
    ]

    _PCI_DEVICES_LIST_WITHOUT_GPU = [
        # This is an NVIDIA device, but not a GPU card:
        MockLspciDevice(cls_name='VGA compatible controller',
                        vendor_name='NVIDIA Corporation'),
    ]

    _PCI_DEVICES_LIST_WITH_NVIDIA_GPU = [
        # This is an NVIDIA device, but not a GPU card:
        MockLspciDevice(cls_name='VGA compatible controller',
                        vendor_name='NVIDIA Corporation'),
        # This is an NVIDIA GPU card:
        MockLspciDevice(cls_name='3D controller',
                        vendor_name='NVIDIA Corporation'),
    ]

    def setUp(self):
        super().setUp(src.charm, self._PATCHES)
        self.harness = Harness(src.charm.NovaComputeNvidiaVgpuCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_has_nvidia_gpu_hardware_with_hw(self):
        self.SimpleParser.return_value = MagicMock()
        self.SimpleParser.return_value.run.return_value = (
            self._PCI_DEVICES_LIST_WITH_NVIDIA_GPU)
        self.assertTrue(
            self.harness.charm._has_nvidia_gpu_hardware_notcached())

    def test_has_nvidia_gpu_hardware_without_hw(self):
        self.SimpleParser.return_value = MagicMock()
        self.SimpleParser.return_value.run.return_value = (
            self._PCI_DEVICES_LIST_WITHOUT_GPU)
        self.assertFalse(
            self.harness.charm._has_nvidia_gpu_hardware_notcached())

    def test_init(self):
        self.assertEqual(
            self.harness.framework.model.app.name,
            'nova-compute-nvidia-vgpu')
        self.assertFalse(self.harness.charm._stored.is_started)
        self.assertIsNone(
            self.harness.charm._stored.last_installed_resource_hash)

    def test_start(self):
        self.harness.charm.on.start.emit()
        self.assertTrue(isinstance(
            self.harness.model.unit.status, ActiveStatus))

    def test_nova_vgpu_relation_joined(self):
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
        self._set_relation_data.assert_called_once_with(
            ANY, 'subordinate_configuration', ANY)
