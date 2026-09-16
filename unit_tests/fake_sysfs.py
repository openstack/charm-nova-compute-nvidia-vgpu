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

"""Fake /sys/bus/pci/devices tree in the NVIDIA vendor-specific VFIO layout.

Mirrors the layout documented in the NVIDIA vGPU user guide: VFs have an
``nvidia/`` directory holding a ``creatable_vgpu_types`` *file* with
``<name>   <id>`` lines and a ``current_vgpu_type`` file reading ``0`` while
no vGPU is configured.
"""

import os
import tempfile

A40_TYPES = [
    (557, 'NVIDIA A40-1Q'),
    (558, 'NVIDIA A40-2Q'),
    (560, 'NVIDIA A40-4Q'),
    (566, 'NVIDIA A40-48Q'),
]


class FakeSysfs:

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def cleanup(self):
        self._tmp.cleanup()

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def _write(self, content, *parts):
        os.makedirs(self.path(*parts[:-1]), exist_ok=True)
        with open(self.path(*parts), 'w') as sysfs_file:
            sysfs_file.write(content)

    def read(self, *parts):
        with open(self.path(*parts)) as sysfs_file:
            return sysfs_file.read()

    def add_pf(self, pf_address, num_vfs=0, total_vfs=None,
               vgpu_types=A40_TYPES, mdev=False):
        """Add a physical GPU with ``num_vfs`` enabled VFs.

        :returns: PCI addresses of the VFs, in virtfn order.
        """
        os.makedirs(self.path(pf_address), exist_ok=True)
        self._write_pci_ids(pf_address)
        self._write('{}\n'.format(
            num_vfs if total_vfs is None else total_vfs),
            pf_address, 'sriov_totalvfs')
        self._write('{}\n'.format(num_vfs), pf_address, 'sriov_numvfs')
        domain_bus, slot = pf_address.rsplit(':', 1)
        first_device = int(slot.split('.')[0], 16)
        vf_addresses = []
        for index in range(num_vfs):
            function = 4 + index
            vf_address = '{}:{:02x}.{:x}'.format(
                domain_bus, first_device + function // 8, function % 8)
            vf_addresses.append(vf_address)
            os.makedirs(self.path(vf_address))
            self._write_pci_ids(vf_address)
            os.symlink('../{}'.format(pf_address),
                       self.path(vf_address, 'physfn'))
            os.symlink('../{}'.format(vf_address),
                       self.path(pf_address, 'virtfn{}'.format(index)))
            if mdev:
                os.makedirs(self.path(vf_address, 'mdev_supported_types'))
            else:
                self.set_creatable(vf_address, vgpu_types)
                self.set_current(vf_address, 0)
        return vf_addresses

    def _write_pci_ids(self, address):
        self._write('0x10de\n', address, 'vendor')
        self._write('0x030200\n', address, 'class')

    def set_creatable(self, vf_address, vgpu_types):
        lines = ''.join('{:<24}{}\n'.format(name, type_id)
                        for type_id, name in vgpu_types)
        self._write(lines, vf_address, 'nvidia', 'creatable_vgpu_types')

    def set_current(self, vf_address, type_id):
        self._write('{}\n'.format(type_id),
                    vf_address, 'nvidia', 'current_vgpu_type')
        if type_id:
            self.set_creatable(vf_address, [])

    def add_nvidia_smi(self, heterogeneous_modes):
        """Create a fake nvidia-smi for the given GPUs.

        :param heterogeneous_modes: PF PCI address mapped to the initial
                                    heterogeneous mode (True/False).
        :returns: Path of the fake nvidia-smi executable.
        """
        smi_dir = self.path('nvidia-smi.d')
        os.makedirs(smi_dir, exist_ok=True)
        gpus = []
        for index, (pf_address, enabled) in enumerate(
                sorted(heterogeneous_modes.items())):
            gpus.append('{}, 0000{}\n'.format(index, pf_address.upper()))
            self._write('Enabled\n' if enabled else 'Disabled\n',
                        'nvidia-smi.d', str(index))
        self._write(''.join(gpus), 'nvidia-smi.d', 'gpus')
        script = self.path('bin', 'nvidia-smi')
        self._write(
            '#!/bin/sh\n'
            'dir={dir}\n'
            'case "$1" in\n'
            '  --query-gpu=*) cat "$dir/gpus" ;;\n'
            '  -q) printf "    vGPU Heterogeneous Mode        : %s\\n" '
            '"$(cat "$dir/$3")" ;;\n'
            '  vgpu) echo "$@" >> "$dir/calls"\n'
            '        if [ "$5" = 1 ]; then echo Enabled > "$dir/$3"\n'
            '        else echo Disabled > "$dir/$3"; fi ;;\n'
            'esac\n'.format(dir=smi_dir), 'bin', 'nvidia-smi')
        os.chmod(script, 0o755)
        return script

    def nvidia_smi_calls(self):
        try:
            return self.read('nvidia-smi.d', 'calls').splitlines()
        except FileNotFoundError:
            return []
