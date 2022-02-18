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

from mock import patch

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
