# Copyright 2026 Canonical Ltd
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

from mock import patch

sys.path.append('src')  # noqa
sys.path.append('unit_tests')  # noqa

import vfio_utils
from fake_sysfs import FakeSysfs

PF = '0000:41:00.0'


class FakeSysfsTestCase(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.sysfs = FakeSysfs()
        self.addCleanup(self.sysfs.cleanup)
        patcher = patch.object(vfio_utils, 'SYS_PCI_BASE', self.sysfs.root)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestSysfsLayer(FakeSysfsTestCase):

    def test_parse_creatable_vgpu_types_documented_layout(self):
        text = ('NVIDIA A40-1Q           557\n'
                'NVIDIA A40-4Q           560\n')
        self.assertEqual(vfio_utils.parse_creatable_vgpu_types(text), [
            {'id': 557, 'name': 'A40-1Q'},
            {'id': 560, 'name': 'A40-4Q'},
        ])

    def test_parse_creatable_vgpu_types_id_first_layout(self):
        text = ('ID    : vGPU Name\n'
                '558   : NVIDIA A40-2Q\n')
        self.assertEqual(vfio_utils.parse_creatable_vgpu_types(text),
                         [{'id': 558, 'name': 'A40-2Q'}])

    def test_parse_creatable_vgpu_types_empty(self):
        self.assertEqual(vfio_utils.parse_creatable_vgpu_types(''), [])

    def test_normalize_profile_name(self):
        self.assertEqual(vfio_utils.normalize_profile_name(' NVIDIA A40-4Q'),
                         'A40-4Q')
        self.assertEqual(vfio_utils.normalize_profile_name('A40-4Q'),
                         'A40-4Q')

    def test_list_vfs_orders_numerically(self):
        vf_addresses = self.sysfs.add_pf(PF, num_vfs=12)

        vfs = vfio_utils.list_vfs(PF)

        self.assertEqual([vf['index'] for vf in vfs], list(range(12)))
        self.assertEqual([vf['pci_address'] for vf in vfs], vf_addresses)

    def test_list_vfs_unknown_pf(self):
        self.assertEqual(vfio_utils.list_vfs(PF), [])

    def test_vf_pci_address(self):
        vf_addresses = self.sysfs.add_pf(PF, num_vfs=2)
        self.assertEqual(vfio_utils.vf_pci_address(PF, 1), vf_addresses[1])
        self.assertIsNone(vfio_utils.vf_pci_address(PF, 5))

    def test_read_current_vgpu_type(self):
        vf0, vf1 = self.sysfs.add_pf(PF, num_vfs=2)
        self.sysfs.set_current(vf1, 560)

        self.assertIsNone(vfio_utils.read_current_vgpu_type(vf0))
        self.assertEqual(vfio_utils.read_current_vgpu_type(vf1), 560)
        self.assertIsNone(vfio_utils.read_current_vgpu_type('0000:99:00.4'))

    def test_read_creatable_vgpu_types(self):
        vf0, vf1 = self.sysfs.add_pf(PF, num_vfs=2)
        self.sysfs.set_current(vf1, 560)

        self.assertIn({'id': 560, 'name': 'A40-4Q'},
                      vfio_utils.read_creatable_vgpu_types(vf0))
        self.assertEqual(vfio_utils.read_creatable_vgpu_types(vf1), [])

    def test_pf_vf_detection(self):
        vf0, = self.sysfs.add_pf(PF, num_vfs=1, total_vfs=32)

        self.assertFalse(vfio_utils.is_virtual_function(PF))
        self.assertTrue(vfio_utils.is_virtual_function(vf0))
        self.assertEqual(vfio_utils.sriov_total_vfs(PF), 32)
        self.assertTrue(vfio_utils.has_vendor_vfio_dir(vf0))
        self.assertFalse(vfio_utils.has_mdev_supported_types(vf0))
