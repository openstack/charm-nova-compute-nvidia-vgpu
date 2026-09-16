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

import os
import sys
import unittest

from mock import patch

sys.path.append('src')  # noqa
sys.path.append('unit_tests')  # noqa

import vfio_utils
from fake_sysfs import FakeSysfs
from vfio_utils import (
    DesiredState,
    IntentNotSatisfiable,
    InvalidGpuProfiles,
    VfMapping,
    reconcile_vf_profiles,
)

PF = '0000:41:00.0'
PF2 = '0000:81:00.0'
A40_CATALOG = {557: 'A40-1Q', 558: 'A40-2Q', 560: 'A40-4Q', 566: 'A40-48Q'}


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


class TestParseGpuProfiles(unittest.TestCase):

    def test_empty(self):
        for raw in (None, '', '  ', '{}'):
            self.assertEqual(vfio_utils.parse_gpu_profiles(raw), {})

    def test_valid(self):
        raw = ('{"0000:41:00.0": [{"type": "NVIDIA A40-4Q", "count": 2}],'
               ' "0000:81:00.0": [{"type": "A40-2Q", "count": 4,'
               ' "policy": "fail"}]}')
        self.assertEqual(vfio_utils.parse_gpu_profiles(raw), {
            PF: [{'type': 'A40-4Q', 'count': 2, 'policy': 'ignore'}],
            PF2: [{'type': 'A40-2Q', 'count': 4, 'policy': 'fail'}],
        })

    def test_single_type_per_gpu(self):
        raw = ('{"0000:41:00.0": [{"type": "A40-4Q", "count": 2},'
               ' {"type": "A40-2Q", "count": 4}]}')
        with self.assertRaisesRegex(
                InvalidGpuProfiles,
                r'^gpu-profiles: 0000:41:00.0 lists A40-4Q, A40-2Q; Nova '
                r'supports a single vGPU type per GPU$'):
            vfio_utils.parse_gpu_profiles(raw)

    def test_gpu_without_profile(self):
        self.assertEqual(
            vfio_utils.parse_gpu_profiles('{"0000:41:00.0": []}'), {PF: []})

    def test_invalid(self):
        invalid = [
            '{not yaml',
            '[1, 2]',
            '{"41:00.0": []}',
            '{"0000:41:00.0": {"type": "A40-4Q"}}',
            '{"0000:41:00.0": ["A40-4Q"]}',
            '{"0000:41:00.0": [{"count": 1}]}',
            '{"0000:41:00.0": [{"type": "A40-4Q"}]}',
            '{"0000:41:00.0": [{"type": "A40-4Q", "count": 0}]}',
            '{"0000:41:00.0": [{"type": "A40-4Q", "count": true}]}',
            '{"0000:41:00.0": [{"type": "A40-4Q", "count": 1, '
            '"policy": "maybe"}]}',
            '{"0000:41:00.0": [{"type": "A40-4Q", "count": 1, "x": 1}]}',
            '{"0000:41:00.0": [{"type": "A40-4Q", "count": 1},'
            ' {"type": "A40-4Q", "count": 1}]}',
        ]
        for raw in invalid:
            with self.assertRaises(InvalidGpuProfiles, msg=raw):
                vfio_utils.parse_gpu_profiles(raw)


def _spec(name, count, policy='ignore'):
    return {'type': name, 'count': count, 'policy': policy}


def _vfs(*current):
    return [{'index': index, 'current': type_id}
            for index, type_id in enumerate(current)]


def _profiles(state):
    return [(vf.pf_pci_address, vf.index, vf.profile['id'], vf.remove)
            for vf in state.vfs]


class TestReconcileVfProfiles(unittest.TestCase):

    def test_fresh_assignment(self):
        intent = {PF: [_spec('A40-4Q', 3)]}

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(None, None, None, None)}, DesiredState(),
            {PF: A40_CATALOG})

        self.assertEqual(_profiles(result),
                         [(PF, 0, 560, False), (PF, 1, 560, False),
                          (PF, 2, 560, False)])
        self.assertEqual(result.vfs[0].profile['name'], 'A40-4Q')

    def test_deterministic(self):
        intent = {PF: [_spec('A40-4Q', 3)]}
        args = (intent, {PF: _vfs(*[None] * 8)}, DesiredState(),
                {PF: A40_CATALOG})
        self.assertEqual(reconcile_vf_profiles(*args),
                         reconcile_vf_profiles(*args))

    def test_sticky_current_profiles(self):
        intent = {PF: [_spec('A40-4Q', 2)]}

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(None, 560, None, 560)}, DesiredState(),
            {PF: A40_CATALOG})

        self.assertEqual(_profiles(result),
                         [(PF, 1, 560, False), (PF, 3, 560, False)])

    def test_sticky_recorded_state_when_vfs_are_free(self):
        intent = {PF: [_spec('A40-4Q', 2)]}
        existing = DesiredState(vfs=[
            VfMapping(PF, 1, {'id': 560, 'name': 'A40-4Q'}),
            VfMapping(PF, 3, {'id': 560, 'name': 'A40-4Q'}),
        ])

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(None, None, None, None)}, existing,
            {PF: A40_CATALOG})

        self.assertEqual(_profiles(result),
                         [(PF, 1, 560, False), (PF, 3, 560, False)])

    def test_names_come_from_catalog(self):
        intent = {PF: [_spec('A40-4Q', 1)]}

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(560)}, DesiredState(), {PF: A40_CATALOG})

        self.assertEqual(result.vfs[0].profile,
                         {'id': 560, 'name': 'A40-4Q'})

    def test_count_reduction_marks_surplus_vfs_for_removal(self):
        intent = {PF: [_spec('A40-4Q', 1)]}

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(560, 560, 560)}, DesiredState(),
            {PF: A40_CATALOG})

        self.assertEqual(_profiles(result),
                         [(PF, 0, 560, False),
                          (PF, 1, 560, True),
                          (PF, 2, 560, True)])

    def test_count_increase_clears_removal_markers(self):
        existing = DesiredState(vfs=[
            VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'}),
            VfMapping(PF, 1, {'id': 560, 'name': 'A40-4Q'}, remove=True),
        ])
        intent = {PF: [_spec('A40-4Q', 2)]}

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(560, 560)}, existing, {PF: A40_CATALOG})

        self.assertEqual(_profiles(result),
                         [(PF, 0, 560, False), (PF, 1, 560, False)])

    def test_reassigns_vf_whose_profile_left_intent(self):
        intent = {PF: [_spec('A40-2Q', 2)]}

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(560, 558)}, DesiredState(), {PF: A40_CATALOG})

        self.assertEqual(_profiles(result),
                         [(PF, 0, 558, False), (PF, 1, 558, False)])

    def test_fail_policy_not_enough_vfs(self):
        intent = {PF: [_spec('A40-4Q', 4, 'fail')]}
        with self.assertRaisesRegex(IntentNotSatisfiable, 'no free VF'):
            reconcile_vf_profiles(intent, {PF: _vfs(None, None)},
                                  DesiredState(), {PF: A40_CATALOG})

    def test_fail_policy_unknown_profile(self):
        intent = {PF: [_spec('L40S-4Q', 1, 'fail')]}
        with self.assertRaisesRegex(IntentNotSatisfiable, 'L40S-4Q'):
            reconcile_vf_profiles(intent, {PF: _vfs(None)}, DesiredState(),
                                  {PF: A40_CATALOG})

    def test_fail_policy_frame_buffer_exhausted(self):
        intent = {PF: [_spec('A40-4Q', 13, 'fail')]}
        with self.assertRaisesRegex(IntentNotSatisfiable, 'frame buffer'):
            reconcile_vf_profiles(intent, {PF: _vfs(*[None] * 16)},
                                  DesiredState(), {PF: A40_CATALOG})

    def test_ignore_policy_returns_available_assignments(self):
        intent = {PF: [_spec('A40-4Q', 4)], PF2: [_spec('L40S-4Q', 1)]}

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(None, None), PF2: _vfs(None)}, DesiredState(),
            {PF: A40_CATALOG, PF2: A40_CATALOG})

        self.assertEqual(_profiles(result),
                         [(PF, 0, 560, False), (PF, 1, 560, False)])

    def test_ignore_policy_frame_buffer_exhausted(self):
        intent = {PF: [_spec('A40-4Q', 16)]}

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(*[None] * 16)}, DesiredState(),
            {PF: A40_CATALOG})

        self.assertEqual([index for _, index, _, _ in _profiles(result)],
                         list(range(12)))

    def test_keeps_recorded_state_when_vfs_not_visible(self):
        intent = {PF: [_spec('A40-4Q', 1)]}
        existing = DesiredState(vfs=[
            VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'}),
            VfMapping(PF2, 0, {'id': 558, 'name': 'A40-2Q'}),
        ])

        result = reconcile_vf_profiles(intent, {}, existing,
                                       {PF: A40_CATALOG})

        self.assertEqual(_profiles(result), [(PF, 0, 560, False)])

    def test_keeps_recorded_state_when_catalog_unknown(self):
        intent = {PF: [_spec('A40-4Q', 1)]}
        existing = DesiredState(vfs=[
            VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'})])

        result = reconcile_vf_profiles(intent, {PF: _vfs(560)}, existing, {})

        self.assertEqual(_profiles(result), [(PF, 0, 560, False)])

    def test_fail_policy_when_vfs_not_visible(self):
        intent = {PF: [_spec('A40-4Q', 1, 'fail')]}
        existing = DesiredState(vfs=[
            VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'})])

        with self.assertRaisesRegex(
                IntentNotSatisfiable, 'no VFs of .* are available'):
            reconcile_vf_profiles(intent, {}, existing, {PF: A40_CATALOG})

    def test_fail_policy_when_catalog_unknown(self):
        intent = {PF: [_spec('A40-4Q', 1, 'fail')]}

        with self.assertRaisesRegex(
                IntentNotSatisfiable, 'vGPU types of .* are unknown'):
            reconcile_vf_profiles(
                intent, {PF: _vfs(560)}, DesiredState(), {})

    def test_same_vf_index_on_different_pfs_remains_distinct(self):
        intent = {PF: [_spec('A40-4Q', 1)], PF2: [_spec('A40-2Q', 1)]}

        result = reconcile_vf_profiles(
            intent, {PF: _vfs(None), PF2: _vfs(None)}, DesiredState(),
            {PF: A40_CATALOG, PF2: A40_CATALOG})

        self.assertEqual(_profiles(result),
                         [(PF, 0, 560, False), (PF2, 0, 558, False)])


class TestStateAndCatalogIO(FakeSysfsTestCase):

    def test_desired_state_roundtrip(self):
        path = self.sysfs.path('state.yaml')
        state = DesiredState(vfs=[
            VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'}),
            VfMapping(PF2, 1, {'id': 558, 'name': 'A40-2Q'}, remove=True),
        ])

        vfio_utils.write_desired_state(state, path)

        self.assertEqual(vfio_utils.read_desired_state(path), state)
        self.assertFalse(os.path.exists(path + '.tmp'))

    def test_state_file_without_remove_key(self):
        path = self.sysfs.path('state.yaml')
        with open(path, 'w') as state_file:
            state_file.write(
                'vfs:\n'
                '- pf_pci_address: 0000:41:00.0\n'
                '  index: 0\n'
                '  profile: {id: 560, name: A40-4Q}\n')

        state = vfio_utils.read_desired_state(path)

        self.assertEqual(
            state.vfs, [VfMapping(PF, 0, {'id': 560, 'name': 'A40-4Q'})])

    def test_state_ignores_unknown_keys(self):
        path = self.sysfs.path('state.yaml')
        with open(path, 'w') as state_file:
            state_file.write('heterogeneous_mode: true\nvfs: []\n')
        self.assertEqual(vfio_utils.read_desired_state(path), DesiredState())

    def test_read_nonexistent_state(self):
        self.assertEqual(
            vfio_utils.read_desired_state(self.sysfs.path('missing.yaml')),
            DesiredState())

    def test_catalog_roundtrip_and_update(self):
        path = self.sysfs.path('catalog.yaml')
        vf0, vf1 = self.sysfs.add_pf(PF, num_vfs=2)
        self.sysfs.set_current(vf0, 560)

        catalog = vfio_utils.update_catalog(
            {PF: {999: 'A40-99Q'}}, {PF: vfio_utils.list_vfs(PF)})
        vfio_utils.write_catalog(catalog, path)

        self.assertEqual(vfio_utils.read_catalog(path),
                         {PF: {**A40_CATALOG, 999: 'A40-99Q'}})
        self.assertEqual(vfio_utils.read_catalog(self.sysfs.path('none')), {})
