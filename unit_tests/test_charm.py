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
from src.charm import NovaComputeNvidiaVgpuCharm
from ops.model import ActiveStatus
from ops.testing import Harness


class TestNovaComputeNvidiaVgpuCharm(unittest.TestCase):

    def setUp(self):
        self.harness = Harness(NovaComputeNvidiaVgpuCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_install(self):
        self.assertEqual(
            self.harness.framework.model.app.name,
            'nova-compute-nvidia-vgpu')
        # Test that charm is active upon installation.
        self.harness.charm.on.install.emit()
        self.assertTrue(isinstance(
            self.harness.model.unit.status, ActiveStatus))
