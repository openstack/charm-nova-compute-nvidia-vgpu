#! /usr/bin/env python3

# Copyright 2021 Canonical Ltd
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

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus


class NovaComputeNvidiaVgpuCharm(CharmBase):

    _stored = StoredState()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.framework.observe(self.on.install, self._on_install)

    def _on_install(self, _):
        self.unit.status = ActiveStatus('Unit is ready')


if __name__ == '__main__':
    main(NovaComputeNvidiaVgpuCharm)
