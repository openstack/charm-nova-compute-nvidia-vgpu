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
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from mock import patch

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
        'set_principal_unit_relation_data',
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
        # principal nova-compute.
        self.assertTrue(self.set_principal_unit_relation_data.called)

    def _run_list_vgpu_types_action(self, output):
        """Invoke the list-vgpu-types action handler with a mocked event.

        The charm base directory is redirected into a temporary directory so
        that the test writes its output file there rather than into the real
        charm directory.

        :param output: The value list_vgpu_types() should return.
        :type output: str
        :returns: A tuple of (results dict passed to set_results, the results
                  directory created under the charm base directory).
        :rtype: Tuple[dict, str]
        """
        charm_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, charm_dir)
        self.harness.charm.framework.charm_dir = Path(charm_dir)
        results_dir = os.path.join(charm_dir, charm.VGPU_TYPES_RESULTS_DIRNAME)

        with patch.object(charm, 'list_vgpu_types', return_value=output):
            action_output = self.harness.run_action('list-vgpu-types')

        return action_output.results, results_dir

    def _written_file(self, results_dir):
        """Return the single file written into the results directory."""
        files = os.listdir(results_dir)
        self.assertEqual(len(files), 1)
        return os.path.join(results_dir, files[0])

    def test_list_vgpu_types_action_small_output(self):
        output = '\n'.join([
            'nvidia-301, 0000:41:00.0, GRID V100-16C, foo',
            'nvidia-302, 0000:c1:00.0, GRID V100-8C, bar',
        ])

        results, results_dir = self._run_list_vgpu_types_action(output)

        self.assertEqual(results['vgpu-types-count'], 2)
        # The result points at the file via a 'juju ssh ... cat' hint.
        output_file = self._written_file(results_dir)
        self.assertIn(output_file, results['result'])
        self.assertIn('juju ssh', results['result'])
        # The full listing is written to that file.
        with open(output_file) as f:
            self.assertEqual(f.read(), output + '\n')

    def test_list_vgpu_types_action_large_output(self):
        # Simulate a host with many GPUs (e.g. SR-IOV vGPU) whose full listing
        # is far larger than what can be passed to `action-set` on the command
        # line. This is the scenario that previously raised
        # "OSError: [Errno 7] Argument list too long".
        line = ('nvidia-1145, 0000:59:00.4, NVIDIA L40S-1B, num_heads=4, '
                'frl_config=45, framebuffer=1024M, max_resolution=5120x2880, '
                'max_instance=32')
        lines = [line] * 3000
        output = '\n'.join(lines)
        self.assertGreater(len(output), 128 * 1024)

        results, results_dir = self._run_list_vgpu_types_action(output)

        # The whole listing is written to the file in full.
        self.assertEqual(results['vgpu-types-count'], len(lines))
        output_file = self._written_file(results_dir)
        with open(output_file) as f:
            self.assertEqual(f.read(), output + '\n')

    def test_list_vgpu_types_action_no_gpu(self):
        results, results_dir = self._run_list_vgpu_types_action('')

        self.assertEqual(results['vgpu-types-count'], 0)
        # An (empty) file is still written and referenced.
        output_file = self._written_file(results_dir)
        self.assertIn(output_file, results['result'])
        with open(output_file) as f:
            self.assertEqual(f.read(), '')
