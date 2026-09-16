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

import os
import sys
import unittest

from mock import MagicMock, patch

sys.path.append('src')  # noqa
sys.path.append('unit_tests')  # noqa

import nvidia_utils
import vfio_utils
from fake_sysfs import FakeSysfs


class MockLspciProperty:
    def __init__(self, name):
        self.name = name


class MockLspciSlot:
    def __init__(self, domain=0, bus=0, device=0, function=0):
        self.domain = domain
        self.bus = bus
        self.device = device
        self.function = function


class MockLspciDevice:
    def __init__(self, cls_name, vendor_name, slot=None):
        self.cls = MockLspciProperty(cls_name)
        self.vendor = MockLspciProperty(vendor_name)
        self.slot = slot or MockLspciSlot(bus=0x41)


class TestNvidiaUtils(unittest.TestCase):

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

    @patch('nvidia_utils.SimpleParser')
    def test_has_nvidia_gpu_hardware_with_hw(self, lspci_parser_mock):
        lspci_parser_mock.return_value.run.return_value = (
            self._PCI_DEVICES_LIST_WITH_NVIDIA_GPU)
        self.assertEqual(
            nvidia_utils._has_nvidia_gpu_hardware_notcached(),
            (True, 1)
        )

    @patch('nvidia_utils.SimpleParser')
    def test_has_nvidia_gpu_hardware_without_hw(self, lspci_parser_mock):
        lspci_parser_mock.return_value.run.return_value = (
            self._PCI_DEVICES_LIST_WITHOUT_GPU)
        self.assertEqual(
            nvidia_utils._has_nvidia_gpu_hardware_notcached(),
            (False, 0)
        )

    @patch('nvidia_utils.Path')
    @patch('nvidia_utils.os.listdir')
    @patch('nvidia_utils.os.walk')
    def test_list_vgpu_types(self, os_walk_mock, os_listdir_mock, path_mock):
        os_walk_mock.return_value = [
            ('/sys/devices/pci0000:40/0000:40:03.1/0000:41:00.0',
             ['mdev_supported_types'], []),
            ('/sys/devices/pci0000:c0/0000:c0:03.1/0000:c1:00.0',
             ['mdev_supported_types'], []),
        ]
        os_listdir_mock.side_effect = [
            ['nvidia-301'],
        ]
        path_mock_obj = MagicMock()
        path_mock.return_value = path_mock_obj
        path_mock_obj.read_text.side_effect = [
            '0x030000',  # causes device to not be included
            '0x030200',
            'GRID V100-16C',
            ('num_heads=1, frl_config=60, framebuffer=16384M, '
             'max_resolution=4096x2160, max_instance=1'),
        ]

        expected_output = '\n'.join([
            ('nvidia-301, 0000:c1:00.0, GRID V100-16C, num_heads=1, '
             'frl_config=60, framebuffer=16384M, max_resolution=4096x2160, '
             'max_instance=1'),
        ])
        self.assertEqual(nvidia_utils.list_vgpu_types(), expected_output)


class TestFindNvidiaGpuPciAddresses(unittest.TestCase):

    @patch('nvidia_utils.SimpleParser')
    def test_find_nvidia_gpu_pci_addresses(self, lspci_parser_mock):
        lspci_parser_mock.return_value.run.return_value = [
            # A non-GPU NVIDIA device, filtered out:
            MockLspciDevice(
                cls_name='VGA compatible controller',
                vendor_name='NVIDIA Corporation',
                slot=MockLspciSlot(domain=0, bus=0x41, device=0x00,
                                   function=0)),
            # An NVIDIA GPU card:
            MockLspciDevice(
                cls_name='3D controller',
                vendor_name='NVIDIA Corporation',
                slot=MockLspciSlot(domain=0, bus=0x41, device=0x00,
                                   function=0)),
        ]
        self.assertEqual(
            nvidia_utils._find_nvidia_gpu_pci_addresses(),
            ['0000:41:00.0'])

    @patch('nvidia_utils.SimpleParser')
    def test_find_nvidia_gpu_pci_addresses_no_gpu(self, lspci_parser_mock):
        lspci_parser_mock.return_value.run.return_value = [
            MockLspciDevice(
                cls_name='VGA compatible controller',
                vendor_name='NVIDIA Corporation',
                slot=MockLspciSlot(domain=0, bus=0x41, device=0x00,
                                   function=0)),
        ]
        self.assertEqual(nvidia_utils._find_nvidia_gpu_pci_addresses(), [])


class FakeSysfsTestCase(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.sysfs = FakeSysfs()
        self.addCleanup(self.sysfs.cleanup)
        patcher = patch.object(vfio_utils, 'SYS_PCI_BASE', self.sysfs.root)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestFindNvidiaGpuPhysicalFunctions(FakeSysfsTestCase):

    @patch('nvidia_utils.SimpleParser')
    def test_virtual_functions_are_skipped(self, lspci_parser_mock):
        self.sysfs.add_pf('0000:41:00.0', num_vfs=2)
        lspci_parser_mock.return_value.run.return_value = [
            MockLspciDevice('3D controller', 'NVIDIA Corporation',
                            MockLspciSlot(bus=0x41, device=0, function=0)),
            MockLspciDevice('3D controller', 'NVIDIA Corporation',
                            MockLspciSlot(bus=0x41, device=0, function=4)),
            MockLspciDevice('3D controller', 'NVIDIA Corporation',
                            MockLspciSlot(bus=0x41, device=0, function=5)),
        ]

        self.assertEqual(nvidia_utils._find_nvidia_gpu_pci_addresses(),
                         ['0000:41:00.0'])
        self.assertEqual(nvidia_utils._has_nvidia_gpu_hardware_notcached(),
                         (True, 1))


class TestDetectVgpuMode(FakeSysfsTestCase):

    @patch('nvidia_utils._find_nvidia_gpu_pci_addresses')
    def test_detect_vfio_mode_from_vfs(self, mock_addrs):
        mock_addrs.return_value = ['0000:41:00.0']
        self.sysfs.add_pf('0000:41:00.0', num_vfs=2)
        self.assertEqual(nvidia_utils.detect_vgpu_mode(), 'vfio')

    @patch('nvidia_utils._find_nvidia_gpu_pci_addresses')
    def test_detect_vfio_mode_from_pf(self, mock_addrs):
        mock_addrs.return_value = ['0000:41:00.0']
        os.makedirs(self.sysfs.path('0000:41:00.0', 'nvidia'))
        self.assertEqual(nvidia_utils.detect_vgpu_mode(), 'vfio')

    @patch('nvidia_utils._find_nvidia_gpu_pci_addresses')
    def test_detect_mdev_mode_from_vfs(self, mock_addrs):
        mock_addrs.return_value = ['0000:41:00.0']
        self.sysfs.add_pf('0000:41:00.0', num_vfs=2, mdev=True)
        self.assertEqual(nvidia_utils.detect_vgpu_mode(), 'mdev')

    @patch('nvidia_utils._find_nvidia_gpu_pci_addresses')
    def test_detect_mdev_mode_from_pf(self, mock_addrs):
        mock_addrs.return_value = ['0000:41:00.0']
        os.makedirs(self.sysfs.path('0000:41:00.0', 'mdev_supported_types'))
        self.assertEqual(nvidia_utils.detect_vgpu_mode(), 'mdev')

    @patch('nvidia_utils._find_nvidia_gpu_pci_addresses')
    def test_detect_vfio_has_priority(self, mock_addrs):
        mock_addrs.return_value = ['0000:41:00.0', '0000:81:00.0']
        self.sysfs.add_pf('0000:41:00.0', num_vfs=1, mdev=True)
        self.sysfs.add_pf('0000:81:00.0', num_vfs=1)
        self.assertEqual(nvidia_utils.detect_vgpu_mode(), 'vfio')

    @patch('nvidia_utils._find_nvidia_gpu_pci_addresses')
    def test_detect_none_when_vfs_not_enabled(self, mock_addrs):
        mock_addrs.return_value = ['0000:41:00.0']
        self.sysfs.add_pf('0000:41:00.0', num_vfs=0, total_vfs=32)
        self.assertEqual(nvidia_utils.detect_vgpu_mode(), 'none')

    @patch('nvidia_utils._find_nvidia_gpu_pci_addresses')
    def test_detect_none_mode(self, mock_addrs):
        mock_addrs.return_value = []
        self.assertEqual(nvidia_utils.detect_vgpu_mode(), 'none')

    def test_detect_with_override_mdev(self):
        self.assertEqual(
            nvidia_utils.detect_vgpu_mode_with_override('mdev'), 'mdev')

    def test_detect_with_override_vfio(self):
        self.assertEqual(
            nvidia_utils.detect_vgpu_mode_with_override('vfio'), 'vfio')

    @patch('nvidia_utils.detect_vgpu_mode')
    def test_detect_with_override_auto(self, mock_detect):
        mock_detect.return_value = 'vfio'
        self.assertEqual(
            nvidia_utils.detect_vgpu_mode_with_override('auto'), 'vfio')


class TestListVgpuTypesVfio(FakeSysfsTestCase):

    @patch('nvidia_utils.vfio_utils.read_catalog')
    @patch('nvidia_utils._find_nvidia_gpu_pci_addresses')
    def test_list_vgpu_types_vfio(self, mock_addrs, mock_catalog):
        mock_addrs.return_value = ['0000:41:00.0']
        mock_catalog.return_value = {'0000:41:00.0': {560: 'A40-4Q'}}
        vf0, vf1 = self.sysfs.add_pf('0000:41:00.0', num_vfs=2)
        self.sysfs.set_current(vf0, 560)

        lines = nvidia_utils.list_vgpu_types_vfio().splitlines()

        self.assertEqual(lines[0], '560, 0000:41:00.4, A40-4Q (assigned)')
        self.assertIn('560, 0000:41:00.5, A40-4Q', lines)
        self.assertIn('558, 0000:41:00.5, A40-2Q', lines)
        self.assertFalse(any('NVIDIA' in line for line in lines))
