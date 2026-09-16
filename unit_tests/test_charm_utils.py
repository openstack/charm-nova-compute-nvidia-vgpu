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

import json
import os
import sys
import unittest
from pathlib import PosixPath

from mock import ANY, MagicMock, call, patch

sys.path.append('src')  # noqa
sys.path.append('unit_tests')  # noqa

from ops.model import (
    ActiveStatus,
    BlockedStatus,
)

import charm_utils
from fake_sysfs import FakeSysfs
from vfio_utils import DesiredState, VfMapping


class TestCharmUtils(unittest.TestCase):

    @patch('nvidia_utils.has_nvidia_gpu_hardware')
    def test_is_nvidia_software_to_be_installed(self,
                                                has_nvidia_gpu_hardware_mock):
        has_nvidia_gpu_hardware_mock.return_value = True, 1
        self.assertTrue(
            charm_utils.is_nvidia_software_to_be_installed_notcached({
                'force-install-nvidia-vgpu': False}))

        has_nvidia_gpu_hardware_mock.return_value = False, 0
        self.assertTrue(
            charm_utils.is_nvidia_software_to_be_installed_notcached({
                'force-install-nvidia-vgpu': True}))
        self.assertFalse(
            charm_utils.is_nvidia_software_to_be_installed_notcached({
                'force-install-nvidia-vgpu': False}))

    @patch('nvidia_utils.update_initramfs')
    @patch('nvidia_utils.render')
    @patch('charm_utils.apt_install')
    @patch('charm_utils._path_and_hash_nvidia_resource')
    @patch('charm_utils.is_nvidia_software_to_be_installed')
    def test_install_nvidia_software_if_needed(
            self, is_software_to_be_installed_mock, path_and_hash_mock,
            apt_install_mock, render_template_mock, update_initramfs_mock):
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
        render_template_mock.assert_called_once_with(
            'disable-nouveau.conf', ANY, ANY)
        update_initramfs_mock.assert_called_once_with()

    @patch('charm_utils.nvidia_utils.detect_vgpu_mode_with_override')
    @patch('charm_utils.ows_check_services_running')
    @patch('charm_utils.is_nvidia_software_to_be_installed')
    @patch('nvidia_utils.installed_nvidia_software_versions')
    @patch('nvidia_utils.has_nvidia_gpu_hardware')
    def test_check_status(
            self, has_hw_mock, installed_sw_mock, is_sw_to_be_installed_mock,
            check_services_running_mock, detect_mode_mock):
        detect_mode_mock.return_value = 'none'
        has_hw_mock.return_value = True, 1
        installed_sw_mock.return_value = ['42', '43']
        is_sw_to_be_installed_mock.return_value = True
        check_services_running_mock.return_value = (None, None)
        self.assertEqual(
            charm_utils.check_status({}, None),
            ActiveStatus(
                'Unit is ready (1 GPU)'
            )
        )

        has_hw_mock.return_value = False, 0
        installed_sw_mock.return_value = ['42', '43']
        is_sw_to_be_installed_mock.return_value = True
        check_services_running_mock.return_value = (None, None)
        self.assertEqual(
            charm_utils.check_status({}, None),
            ActiveStatus(
                'Unit is ready (0 GPU)'
            )
        )

        has_hw_mock.return_value = True, 1
        installed_sw_mock.return_value = []
        is_sw_to_be_installed_mock.return_value = True
        check_services_running_mock.return_value = (None, None)
        self.assertEqual(
            charm_utils.check_status({}, None),
            BlockedStatus(
                'NVIDIA GPU detected, drivers not installed'
            )
        )

        has_hw_mock.return_value = True, 2
        installed_sw_mock.return_value = []
        is_sw_to_be_installed_mock.return_value = False
        check_services_running_mock.return_value = (None, None)
        self.assertEqual(
            charm_utils.check_status({}, None),
            ActiveStatus(
                'Unit is ready (2 GPU)'
            )
        )

        has_hw_mock.return_value = True, 1
        installed_sw_mock.return_value = ['42', '43']
        is_sw_to_be_installed_mock.return_value = True
        check_services_running_mock.return_value = (
            None, 'Services not running that should be: nvidia-vgpu-mgr')
        self.assertEqual(
            charm_utils.check_status({}, None),
            BlockedStatus('manual reboot required')
        )

    @patch('charm_utils.nvidia_utils.detect_vgpu_mode_with_override')
    @patch('nvidia_utils._installed_nvidia_software_packages')
    @patch('charm_utils.get_os_codename_package')
    def test_set_principal_unit_relation_data(self, release_codename_mock,
                                              installed_packages_mock,
                                              detect_mode_mock):
        detect_mode_mock.return_value = 'mdev'
        release_codename_mock.return_value = 'xena'
        installed_packages_mock.return_value = [{
            'name': 'nvidia-vgpu-ubuntu-470',
            'version': '470.68',
            'architecture': 'amd64',
            'description': 'NVIDIA vGPU driver - version 470.68'
        }]
        relation_data_to_be_set = {}
        charm_config = {
            'vgpu-device-mappings': "{'nvidia-35': ['0000:84:00.0']}"
        }
        charm_services = ['nvidia-vgpu-mgr']

        charm_utils.set_principal_unit_relation_data(
            relation_data_to_be_set, charm_config, charm_services)

        self.assertIn(
            '0000:84:00.0',
            relation_data_to_be_set['subordinate_configuration'])
        self.assertIn(
            'nvidia-vgpu-mgr',
            relation_data_to_be_set['services'])
        self.assertIn(
            'nvidia-vgpu-ubuntu-470',
            relation_data_to_be_set['releases-packages-map'])

        relation_data_to_be_set = {}
        charm_config = {
            'vgpu-device-mappings': ''
        }
        charm_utils.set_principal_unit_relation_data(
            relation_data_to_be_set, charm_config, charm_services)
        self.assertEqual(
            '{"nova": {"/etc/nova/nova.conf": {"sections": {"devices": '
            '[["enabled_mdev_types", ""]]}}}}',
            relation_data_to_be_set['subordinate_configuration'])

    @patch('charm_utils.file_hash')
    def test_path_and_hash_nvidia_resource(self, file_hash_mock):
        file_hash_mock.return_value = 'nvidia-software-hash'
        resources = MagicMock()
        resources.fetch.return_value = PosixPath('nvidia-software-path')

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
                         expected_queens_nova_conf_sections)

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

    @patch.object(charm_utils, 'service')
    @patch.object(charm_utils, 'render')
    @patch.object(charm_utils.os, 'chmod')
    @patch.object(charm_utils.shutil, 'copy')
    def test_install_mdev_init_workaround(self, mock_copy, mock_chmod,
                                          mock_render, mock_service):
        charm_config = {
            'vgpu-device-mappings': "{'nvidia-35': ['0000:84:00.0']}"
        }
        charm_utils.install_mdev_init_workaround(charm_config)
        mock_copy.assert_has_calls([call('files/initialise_nova_mdevs.sh',
                                         '/opt/initialise_nova_mdevs.sh')])
        mock_render.assert_has_calls([
            call('remediate_nova_mdevs.py',
                 '/opt/remediate-nova-mdevs',
                 {'mdev_types': {
                     'nvidia-35': ['0000:84:00.0']}},
                 perms=493),
            call('systemd-mdev-workaround.service',
                 '/etc/systemd/system/systemd-mdev-workaround.service', {},
                 perms=420)])


class TestVgpuModeConfig(unittest.TestCase):

    def test_vgpu_mode_default_is_auto(self):
        """vgpu-mode config option defaults to 'auto'."""
        import yaml
        with open('config.yaml') as f:
            config = yaml.safe_load(f)
        self.assertIn('vgpu-mode', config['options'])
        self.assertEqual(config['options']['vgpu-mode']['default'], 'auto')
        self.assertIn('mdev', config['options']['vgpu-mode']['description'])
        self.assertIn('vfio', config['options']['vgpu-mode']['description'])

    def test_gpu_profiles_config_exists(self):
        """gpu-profiles config option exists for VFIO mode."""
        import yaml
        with open('config.yaml') as f:
            config = yaml.safe_load(f)
        self.assertIn('gpu-profiles', config['options'])
        self.assertIsNone(config['options']['gpu-profiles']['default'])

    def test_no_heterogeneous_mode_config(self):
        import yaml
        with open('config.yaml') as f:
            config = yaml.safe_load(f)
        self.assertNotIn('heterogeneous-mode', config['options'])


PF = '0000:41:00.0'
PF2 = '0000:81:00.0'
PROFILES = ('{"0000:41:00.0": [{"type": "A40-4Q", "count": 3}], '
            '"0000:81:00.0": [{"type": "A40-2Q", "count": 1}]}')


class FakeSysfsTestCase(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.sysfs = FakeSysfs()
        self.addCleanup(self.sysfs.cleanup)
        patches = [
            patch.object(charm_utils.vfio_utils, 'SYS_PCI_BASE',
                         self.sysfs.root),
            patch.object(charm_utils.vfio_utils, 'DEFAULT_STATE_FILE',
                         self.sysfs.path('state.yaml')),
            patch.object(charm_utils.vfio_utils, 'DEFAULT_CATALOG_FILE',
                         self.sysfs.path('catalog.yaml')),
            patch('charm_utils.nvidia_utils._find_nvidia_gpu_pci_addresses',
                  return_value=[PF, PF2]),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)


class TestVfioDesiredStateGeneration(FakeSysfsTestCase):

    def test_generate_resolves_names_from_sysfs(self):
        self.sysfs.add_pf(PF, num_vfs=4)
        self.sysfs.add_pf(PF2, num_vfs=2)

        state = charm_utils.generate_vfio_desired_state(
            {'gpu-profiles': PROFILES})

        self.assertEqual(
            [(vf.pf_pci_address, vf.index, vf.profile) for vf in state.vfs],
            [(PF, 0, {'id': 560, 'name': 'A40-4Q'}),
             (PF, 1, {'id': 560, 'name': 'A40-4Q'}),
             (PF, 2, {'id': 560, 'name': 'A40-4Q'}),
             (PF2, 0, {'id': 558, 'name': 'A40-2Q'})])
        self.assertEqual(charm_utils.vfio_utils.read_desired_state(), state)
        self.assertEqual(
            charm_utils.vfio_utils.read_catalog()[PF][560], 'A40-4Q')

    def test_generate_after_reboot_keeps_assignments(self):
        """creatable_vgpu_types is empty once profiles are applied."""
        vf_addresses = {PF: self.sysfs.add_pf(PF, num_vfs=4),
                        PF2: self.sysfs.add_pf(PF2, num_vfs=2)}
        first = charm_utils.generate_vfio_desired_state(
            {'gpu-profiles': PROFILES})
        for vf in first.vfs:
            self.sysfs.set_current(vf_addresses[vf.pf_pci_address][vf.index],
                                   vf.profile['id'])
        for addresses in vf_addresses.values():
            for address in addresses:
                self.sysfs.set_creatable(address, [])

        second = charm_utils.generate_vfio_desired_state(
            {'gpu-profiles': PROFILES})

        self.assertEqual(second, first)

    def test_generate_without_vfs_keeps_recorded_state(self):
        self.sysfs.add_pf(PF, num_vfs=4)
        self.sysfs.add_pf(PF2, num_vfs=2)
        first = charm_utils.generate_vfio_desired_state(
            {'gpu-profiles': PROFILES})
        for index in range(4):
            os.remove(self.sysfs.path(PF, 'virtfn{}'.format(index)))

        second = charm_utils.generate_vfio_desired_state(
            {'gpu-profiles': PROFILES})

        self.assertEqual(second, first)

    def test_generate_rejects_several_types_per_gpu(self):
        self.sysfs.add_pf(PF, num_vfs=4)
        with self.assertRaisesRegex(charm_utils.vfio_utils.InvalidGpuProfiles,
                                    'single vGPU type per GPU'):
            charm_utils.generate_vfio_desired_state({'gpu-profiles': (
                '{"0000:41:00.0": [{"type": "A40-4Q", "count": 2}, '
                '{"type": "A40-2Q", "count": 1}]}')})

    def test_generate_clears_removed_intent(self):
        self.sysfs.add_pf(PF, num_vfs=4)
        charm_utils.generate_vfio_desired_state({'gpu-profiles': PROFILES})

        state = charm_utils.generate_vfio_desired_state({'gpu-profiles': ''})

        self.assertEqual(state.vfs, [])

    def test_missing_gpu_with_fail_policy(self):
        self.sysfs.add_pf(PF, num_vfs=1)
        with self.assertRaisesRegex(
                charm_utils.vfio_utils.IntentNotSatisfiable, '0000:99:00.0'):
            charm_utils.compute_vfio_desired_state({'gpu-profiles': (
                '{"0000:99:00.0": [{"type": "A40-4Q", "count": 1, '
                '"policy": "fail"}]}')})

    def test_missing_gpu_with_ignore_policy(self):
        self.sysfs.add_pf(PF, num_vfs=1)
        state, _ = charm_utils.compute_vfio_desired_state({'gpu-profiles': (
            '{"0000:99:00.0": [{"type": "A40-4Q", "count": 1}]}')})
        self.assertEqual(state.vfs, [])

    def test_split_live_vf_mappings(self):
        vf_addresses = self.sysfs.add_pf(PF, num_vfs=4)
        self.sysfs.add_pf(PF2, num_vfs=2)
        state = charm_utils.generate_vfio_desired_state(
            {'gpu-profiles': PROFILES})
        self.sysfs.set_current(vf_addresses[0], 560)
        self.sysfs.set_current(vf_addresses[1], 558)

        live, pending = charm_utils.split_live_vf_mappings(state)

        self.assertEqual([(vf.index, address) for vf, address in live],
                         [(0, vf_addresses[0])])
        self.assertEqual([(vf.pf_pci_address, vf.index) for vf in pending],
                         [(PF, 1), (PF, 2), (PF2, 0)])

    def test_split_live_vf_mappings_counts_removals(self):
        vf_addresses = self.sysfs.add_pf(PF, num_vfs=3)
        self.sysfs.set_current(vf_addresses[0], 560)
        self.sysfs.set_current(vf_addresses[1], 560)
        state = DesiredState(vfs=[
            VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'}),
            VfMapping(PF, 1, {'id': 560, 'name': 'A40-4Q'}, remove=True),
            VfMapping(PF, 2, {'id': 560, 'name': 'A40-4Q'}, remove=True),
        ])

        live, pending = charm_utils.split_live_vf_mappings(state)

        self.assertEqual([(vf.index, address) for vf, address in live],
                         [(0, vf_addresses[0])])
        self.assertEqual([(vf.pf_pci_address, vf.index) for vf in pending],
                         [(PF, 1)])

        # After the reboot dropped the surplus vGPUs they are no longer
        # pending.
        for address in vf_addresses[1:]:
            self.sysfs.set_current(address, 0)
        live, pending = charm_utils.split_live_vf_mappings(state)

        self.assertEqual([(vf.index, address) for vf, address in live],
                         [(0, vf_addresses[0])])
        self.assertEqual(pending, [])


class TestNovaConfVfio(unittest.TestCase):

    LIVE_VFS = [
        (VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'}), '0000:41:00.4'),
        (VfMapping(PF, 1, {'id': 560, 'name': 'A40-4Q'}), '0000:41:00.5'),
        (VfMapping(PF2, 0, {'id': 558, 'name': 'A40-2Q'}), '0000:81:00.4'),
    ]

    def test_nova_conf_sections_vfio(self):
        sections = charm_utils._nova_conf_sections_vfio(self.LIVE_VFS)

        device_specs = [json.loads(value) for key, value in sections['pci']
                        if key == 'device_spec']
        self.assertEqual(device_specs[0], {
            'vendor_id': '10de',
            'address': '0000:41:00.4',
            'resource_class': 'CUSTOM_NVIDIA_560',
            'managed': 'no',
        })
        self.assertEqual([spec['address'] for spec in device_specs],
                         ['0000:41:00.4', '0000:41:00.5', '0000:81:00.4'])
        aliases = [json.loads(value) for key, value in sections['pci']
                   if key == 'alias']
        self.assertEqual(aliases, [
            {'device_type': 'type-VF', 'resource_class': 'CUSTOM_NVIDIA_558',
             'name': 'A40-2Q'},
            {'device_type': 'type-VF', 'resource_class': 'CUSTOM_NVIDIA_560',
             'name': 'A40-4Q'},
        ])
        self.assertEqual(sections['libvirt'],
                         [('live_migration_permit_post_copy', 'False')])

    def test_controller_pci_alias_fragments(self):
        fragments = charm_utils._controller_pci_alias_fragments(self.LIVE_VFS)
        self.assertEqual([json.loads(f)['name'] for f in fragments],
                         ['A40-2Q', 'A40-4Q'])


class TestVfioRelationData(FakeSysfsTestCase):

    def setUp(self):
        super().setUp()
        patcher = patch('charm_utils.vfio_release_blocker', return_value=None)
        self.release_blocker = patcher.start()
        self.addCleanup(patcher.stop)

    @patch('charm_utils.nvidia_utils.installed_nvidia_software_package_names',
           return_value=[])
    @patch('charm_utils.get_os_codename_package', return_value='caracal')
    def test_only_live_vfs_are_published(self, _release, _packages):
        vf_addresses = self.sysfs.add_pf(PF, num_vfs=4)
        self.sysfs.set_current(vf_addresses[0], 560)
        relation_data = {}

        charm_utils._set_principal_relation_data_vfio(
            relation_data, {'gpu-profiles': PROFILES}, ['nvidia-vgpu-mgr'])

        sections = json.loads(relation_data['subordinate_configuration'])[
            'nova']['/etc/nova/nova.conf']['sections']
        device_specs = [json.loads(value) for key, value in sections['pci']
                        if key == 'device_spec']
        self.assertEqual([spec['address'] for spec in device_specs],
                         [vf_addresses[0]])
        self.assertNotIn('placement', sections)
        self.assertNotIn('workarounds', sections)
        self.assertEqual(
            [json.loads(f)['name'] for f in
             json.loads(relation_data['pci-alias-fragments'])],
            ['A40-4Q'])

    @patch('charm_utils.get_os_codename_package', return_value='caracal')
    def test_unsatisfiable_intent_leaves_relation_untouched(self, _release):
        self.sysfs.add_pf(PF, num_vfs=1)
        relation_data = {}

        charm_utils._set_principal_relation_data_vfio(relation_data, {
            'gpu-profiles': ('{"0000:41:00.0": [{"type": "A40-4Q", '
                             '"count": 2, "policy": "fail"}]}')}, [])

        self.assertEqual(relation_data, {})

    @patch('charm_utils.get_os_codename_package', return_value='caracal')
    def test_invalid_config_leaves_relation_untouched(self, _release):
        relation_data = {}
        charm_utils._set_principal_relation_data_vfio(
            relation_data, {'gpu-profiles': '[1]'}, [])
        self.assertEqual(relation_data, {})

    def test_unsupported_release_leaves_relation_untouched(self):
        self.release_blocker.return_value = 'too old'
        self.sysfs.add_pf(PF, num_vfs=4)
        relation_data = {}
        charm_utils._set_principal_relation_data_vfio(
            relation_data, {'gpu-profiles': PROFILES}, [])
        self.assertEqual(relation_data, {})


class TestVfioStatus(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.dpkg = {'nova-common': {'version': '3:29.2.0-0ubuntu1.8+vgpu1'}}
        self.pending = []
        patches = [
            patch('nvidia_utils.installed_nvidia_software_versions',
                  return_value=['550.54']),
            patch('nvidia_utils.has_nvidia_gpu_hardware',
                  return_value=(True, 1)),
            patch('charm_utils.is_nvidia_software_to_be_installed',
                  return_value=True),
            patch('charm_utils.ows_check_services_running',
                  return_value=(None, None)),
            patch('charm_utils.nvidia_utils.detect_vgpu_mode_with_override',
                  return_value='vfio'),
            patch('charm_utils.compute_vfio_desired_state'),
            patch('charm_utils.get_os_codename_package',
                  return_value='caracal'),
            patch('charm_utils.apt_cache'),
            patch('charm_utils.vfio_utils.read_desired_state',
                  return_value=DesiredState(vfs=[
                      VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'})])),
            patch('charm_utils.split_live_vf_mappings',
                  side_effect=lambda _state: ([], self.pending)),
        ]
        self.mocks = {}
        for patcher in patches:
            self.mocks[patcher.attribute] = patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks['apt_cache'].return_value.dpkg_list.side_effect = (
            lambda packages: self.dpkg)

    def check_status(self):
        return charm_utils.check_status({'vgpu-mode': 'auto'},
                                        ['nvidia-vgpu-mgr'])

    def test_ready(self):
        self.assertEqual(self.check_status(),
                         ActiveStatus('Unit is ready (1 GPU)'))

    def test_unsatisfied_intent(self):
        self.mocks['compute_vfio_desired_state'].side_effect = (
            charm_utils.vfio_utils.IntentNotSatisfiable('no free VF'))
        self.assertEqual(
            self.check_status(),
            BlockedStatus('gpu-profiles cannot be satisfied: no free VF'))

    def test_invalid_config(self):
        self.mocks['compute_vfio_desired_state'].side_effect = (
            charm_utils.vfio_utils.InvalidGpuProfiles('bad gpu-profiles'))
        self.assertEqual(self.check_status(),
                         BlockedStatus('bad gpu-profiles'))

    def test_release_before_caracal(self):
        self.mocks['get_os_codename_package'].return_value = 'zed'
        self.assertEqual(self.check_status(), BlockedStatus(
            'VFIO mode requires OpenStack Caracal with the PCI managed '
            'backport or Epoxy and later (found zed)'))

    def test_dalmatian_has_no_backport(self):
        self.mocks['get_os_codename_package'].return_value = 'dalmatian'
        self.assertIn('found dalmatian', self.check_status().message)

    def test_caracal_without_backport_only_warns(self):
        self.dpkg = {'nova-common': {'version': '3:29.2.0-0ubuntu1.8'}}
        with self.assertLogs(level='WARNING') as logs:
            self.assertEqual(self.check_status(),
                             ActiveStatus('Unit is ready (1 GPU)'))
        self.assertIn('nova-common >= 3:29.2.0-0ubuntu1.8+vgpu1 on Caracal',
                      logs.output[0])
        self.assertIn('(installed: 3:29.2.0-0ubuntu1.8)', logs.output[0])

    def test_caracal_nova_not_installed_only_warns(self):
        self.dpkg = {}
        with self.assertLogs(level='WARNING') as logs:
            self.assertIsInstance(self.check_status(), ActiveStatus)
        self.assertIn('(installed: none)', logs.output[0])

    def test_caracal_with_backport_does_not_warn(self):
        with patch('charm_utils.logging.warning') as warning_mock:
            self.assertIsInstance(self.check_status(), ActiveStatus)
        warning_mock.assert_not_called()

    def test_epoxy_needs_no_version_check(self):
        self.mocks['get_os_codename_package'].return_value = 'epoxy'
        self.dpkg = {}
        self.assertIsInstance(self.check_status(), ActiveStatus)

    def test_pending_reboot(self):
        self.pending = [VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'})]
        self.assertEqual(self.check_status(), BlockedStatus(
            '1 vGPU profile change(s) pending, manual reboot required'))

    def test_mdev_mode_skips_vfio_checks(self):
        self.mocks['detect_vgpu_mode_with_override'].return_value = 'mdev'
        self.mocks['get_os_codename_package'].return_value = 'zed'
        self.pending = [VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'})]
        self.assertIsInstance(self.check_status(), ActiveStatus)
        self.mocks['compute_vfio_desired_state'].assert_not_called()
