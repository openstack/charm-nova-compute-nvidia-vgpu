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

from ops.model import (
    ActiveStatus,
    BlockedStatus,
)

import charm_utils


class TestCharmUtils(unittest.TestCase):

    @patch('nvidia_utils.has_nvidia_gpu_hardware')
    def test_is_nvidia_software_to_be_installed(self,
                                                has_nvidia_gpu_hardware_mock):
        has_nvidia_gpu_hardware_mock.return_value = True
        self.assertTrue(
            charm_utils.is_nvidia_software_to_be_installed_notcached({
                'force-install-nvidia-vgpu': False}))

        has_nvidia_gpu_hardware_mock.return_value = False
        self.assertTrue(
            charm_utils.is_nvidia_software_to_be_installed_notcached({
                'force-install-nvidia-vgpu': True}))
        self.assertFalse(
            charm_utils.is_nvidia_software_to_be_installed_notcached({
                'force-install-nvidia-vgpu': False}))

    @patch('charm_utils.apt_install')
    @patch('charm_utils._path_and_hash_nvidia_resource')
    @patch('charm_utils.is_nvidia_software_to_be_installed')
    def test_install_nvidia_software_if_needed(
            self, is_software_to_be_installed_mock, path_and_hash_mock,
            apt_install_mock):
        is_software_to_be_installed_mock.return_value = True
        unit_stored_state = MagicMock()
        unit_stored_state.last_installed_resource_hash = 'hash-1'

        # If a software package with the exact same hash has already been
        # installed, no new installation should be performed:
        path_and_hash_mock.return_value = (
            'path-to-software',
            'hash-1',
        )
        charm_utils.install_nvidia_software_if_needed(unit_stored_state, None,
                                                      None)
        self.assertFalse(apt_install_mock.called)

        # If there is now a new software package with a different hash,
        # installation should be performed:
        path_and_hash_mock.return_value = (
            'path-to-software',
            'hash-2',
        )
        charm_utils.install_nvidia_software_if_needed(unit_stored_state, None,
                                                      None)
        apt_install_mock.assert_called_once_with(['path-to-software'],
                                                 fatal=True)

    @patch('charm_utils.is_nvidia_software_to_be_installed')
    @patch('nvidia_utils.installed_nvidia_software_versions')
    @patch('nvidia_utils.has_nvidia_gpu_hardware')
    def test_check_status(self, has_hw_mock, installed_sw_mock,
                          is_sw_to_be_installed_mock):
        has_hw_mock.return_value = True
        installed_sw_mock.return_value = ['42', '43']
        is_sw_to_be_installed_mock.return_value = True
        self.assertEqual(
            charm_utils.check_status(None),
            ActiveStatus(
                'Unit is ready: '
                'NVIDIA GPU found; installed NVIDIA software: 42, 43'))

        has_hw_mock.return_value = False
        installed_sw_mock.return_value = ['42', '43']
        is_sw_to_be_installed_mock.return_value = True
        self.assertEqual(
            charm_utils.check_status(None),
            ActiveStatus(
                'Unit is ready: '
                'no NVIDIA GPU found; installed NVIDIA software: 42, 43'))

        has_hw_mock.return_value = True
        installed_sw_mock.return_value = []
        is_sw_to_be_installed_mock.return_value = True
        self.assertEqual(
            charm_utils.check_status(None),
            BlockedStatus(
                'NVIDIA GPU found; no NVIDIA software installed'))

        has_hw_mock.return_value = True
        installed_sw_mock.return_value = []
        is_sw_to_be_installed_mock.return_value = False
        self.assertEqual(
            charm_utils.check_status(None),
            ActiveStatus(
                'Unit is ready: '
                'NVIDIA GPU found; no NVIDIA software installed'))

    @patch('charm_utils.get_os_codename_package')
    def test_set_principal_unit_relation_data(self, release_codename_mock):
        release_codename_mock.return_value = 'xena'
        relation_data_to_be_set = {}
        charm_config = {
            'vgpu-device-mappings': "{'nvidia-35': ['0000:84:00.0']}"
        }

        charm_utils.set_principal_unit_relation_data(relation_data_to_be_set,
                                                     charm_config)

        self.assertIn(
            '0000:84:00.0',
            relation_data_to_be_set['subordinate_configuration'])

    @patch('charm_utils.file_hash')
    def test_path_and_hash_nvidia_resource(self, file_hash_mock):
        file_hash_mock.return_value = 'nvidia-software-hash'
        resources = MagicMock()
        resources.fetch.return_value = 'nvidia-software-path'

        self.assertEqual(charm_utils._path_and_hash_nvidia_resource(resources),
                         ('nvidia-software-path', 'nvidia-software-hash'))

    @patch('charm_utils.get_os_codename_package')
    def test_nova_conf_sections(self, release_codename_mock):
        vgpu_device_mappings = {
            'nvidia-35': ['0000:84:00.0', '0000:85:00.0'],
            'nvidia-36': ['0000:86:00.0'],
        }

        expected_queens_nova_conf_sections = {
            'devices': [
                ('enabled_vgpu_types', 'nvidia-35, nvidia-36'),
            ],
        }
        expected_ussuri_nova_conf_sections = {
            'devices': [
                ('enabled_vgpu_types', 'nvidia-35, nvidia-36'),
            ],
            'vgpu_nvidia-35': [
                ('device_addresses', '0000:84:00.0,0000:85:00.0'),
            ],
            'vgpu_nvidia-36': [
                ('device_addresses', '0000:86:00.0'),
            ],
        }
        expected_xena_nova_conf_sections = {
            'devices': [
                ('enabled_mdev_types', 'nvidia-35, nvidia-36'),
            ],
            'mdev_nvidia-35': [
                ('device_addresses', '0000:84:00.0,0000:85:00.0'),
            ],
            'mdev_nvidia-36': [
                ('device_addresses', '0000:86:00.0'),
            ],
        }

        release_codename_mock.return_value = 'xena'
        self.assertEqual(charm_utils._nova_conf_sections(vgpu_device_mappings),
                         expected_xena_nova_conf_sections)

        release_codename_mock.return_value = None
        self.assertEqual(charm_utils._nova_conf_sections(vgpu_device_mappings),
                         expected_xena_nova_conf_sections)

        release_codename_mock.return_value = 'ussuri'
        self.assertEqual(charm_utils._nova_conf_sections(vgpu_device_mappings),
                         expected_ussuri_nova_conf_sections)

        release_codename_mock.return_value = 'wallaby'
        self.assertEqual(charm_utils._nova_conf_sections(vgpu_device_mappings),
                         expected_ussuri_nova_conf_sections)

        release_codename_mock.return_value = 'queens'
        self.assertEqual(charm_utils._nova_conf_sections(vgpu_device_mappings),
                         expected_queens_nova_conf_sections)

        with self.assertRaises(charm_utils.UnsupportedOpenStackRelease):
            release_codename_mock.return_value = 'pike'
            charm_utils._nova_conf_sections(vgpu_device_mappings)
