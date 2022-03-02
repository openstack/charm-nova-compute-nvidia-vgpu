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

from mock import MagicMock, patch

sys.path.append('src')  # noqa

import nvidia_utils


class MockLspciProperty:
    def __init__(self, name):
        self.name = name


class MockLspciDevice:
    def __init__(self, cls_name, vendor_name):
        self.cls = MockLspciProperty(cls_name)
        self.vendor = MockLspciProperty(vendor_name)


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
        self.assertTrue(nvidia_utils._has_nvidia_gpu_hardware_notcached())

    @patch('nvidia_utils.SimpleParser')
    def test_has_nvidia_gpu_hardware_without_hw(self, lspci_parser_mock):
        lspci_parser_mock.return_value.run.return_value = (
            self._PCI_DEVICES_LIST_WITHOUT_GPU)
        self.assertFalse(nvidia_utils._has_nvidia_gpu_hardware_notcached())

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
            ['nvidia-256', 'nvidia-257'],
            ['nvidia-301'],
        ]
        path_mock_obj = MagicMock()
        path_mock.return_value = path_mock_obj
        path_mock_obj.read_text.side_effect = [
            'GRID RTX6000-1Q',
            ('num_heads=4, frl_config=60, framebuffer=1024M, '
             'max_resolution=5120x2880, max_instance=24'),
            'GRID RTX6000-2Q',
            ('num_heads=4, frl_config=60, framebuffer=2048M, '
             'max_resolution=7680x4320, max_instance=12'),
            'GRID V100-16C',
            ('num_heads=1, frl_config=60, framebuffer=16384M, '
             'max_resolution=4096x2160, max_instance=1'),
        ]

        expected_output = '\n'.join([
            ('nvidia-256, 0000:41:00.0, GRID RTX6000-1Q, num_heads=4, '
             'frl_config=60, framebuffer=1024M, max_resolution=5120x2880, '
             'max_instance=24'),
            ('nvidia-257, 0000:41:00.0, GRID RTX6000-2Q, num_heads=4, '
             'frl_config=60, framebuffer=2048M, max_resolution=7680x4320, '
             'max_instance=12'),
            ('nvidia-301, 0000:c1:00.0, GRID V100-16C, num_heads=1, '
             'frl_config=60, framebuffer=16384M, max_resolution=4096x2160, '
             'max_instance=1'),
        ])
        self.assertEqual(nvidia_utils.list_vgpu_types(), expected_output)
