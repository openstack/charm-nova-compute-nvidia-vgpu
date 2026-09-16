#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0


import os
import re

from ruamel.yaml import YAML


DEFAULT_CATALOG_FILE = '/etc/nvidia-vgpu-catalog.yaml'

SYS_PCI_BASE = '/sys/bus/pci/devices'

# Lines of the vendor-specific VFIO ``creatable_vgpu_types`` file. NVIDIA
# documents the ``<name>   <id>`` layout, e.g. ``NVIDIA A40-4Q   560``; the
# ``<id> : <name>`` layout is accepted too since some driver branches use it.
_TYPE_NAME_FIRST_RE = re.compile(r'^\s*(\S.*?)\s+(\d+)\s*$')
_TYPE_ID_FIRST_RE = re.compile(r'^\s*(\d+)\s*:\s*(\S.*?)\s*$')
_VIRTFN_RE = re.compile(r'^virtfn(\d+)$')


def normalize_profile_name(name):
    """Strip the ``NVIDIA `` vendor prefix sysfs adds to vGPU type names."""
    name = name.strip()
    if name.upper().startswith('NVIDIA '):
        name = name[len('NVIDIA '):].strip()
    return name


def parse_creatable_vgpu_types(text):
    """Parse the content of a VF ``nvidia/creatable_vgpu_types`` file.

    :returns: List of dicts with 'id' (int) and normalized 'name' keys.
    :rtype: List[dict]
    """
    types = []
    for line in text.splitlines():
        match = _TYPE_ID_FIRST_RE.match(line)
        if match:
            type_id, name = match.groups()
        else:
            match = _TYPE_NAME_FIRST_RE.match(line)
            if not match:
                continue
            name, type_id = match.groups()
        types.append({'id': int(type_id),
                      'name': normalize_profile_name(name)})
    return types


def _read_sysfs(address, *parts):
    path = os.path.join(SYS_PCI_BASE, address, *parts)
    with open(path) as sysfs_file:
        return sysfs_file.read()


def is_virtual_function(address):
    return os.path.exists(os.path.join(SYS_PCI_BASE, address, 'physfn'))


def sriov_total_vfs(address):
    try:
        return int(_read_sysfs(address, 'sriov_totalvfs').strip())
    except (OSError, ValueError):
        return 0


def has_vendor_vfio_dir(address):
    return os.path.isdir(os.path.join(SYS_PCI_BASE, address, 'nvidia'))


def has_mdev_supported_types(address):
    return os.path.isdir(
        os.path.join(SYS_PCI_BASE, address, 'mdev_supported_types'))


def list_vfs(pf_address):
    """List the VFs of a physical function, ordered by virtfn index.

    :returns: List of dicts with 'index' and 'pci_address' keys.
    :rtype: List[dict]
    """
    pf_path = os.path.join(SYS_PCI_BASE, pf_address)
    try:
        entries = os.listdir(pf_path)
    except FileNotFoundError:
        return []

    vfs = []
    for entry in entries:
        match = _VIRTFN_RE.match(entry)
        if not match:
            continue
        target = os.readlink(os.path.join(pf_path, entry))
        vfs.append({
            'index': int(match.group(1)),
            'pci_address': os.path.basename(os.path.normpath(target)),
        })
    return sorted(vfs, key=lambda vf: vf['index'])


def vf_pci_address(pf_address, index):
    """Resolve ``<pf>/virtfn<index>`` to the VF PCI address, or None."""
    link = os.path.join(SYS_PCI_BASE, pf_address, 'virtfn{}'.format(index))
    try:
        return os.path.basename(os.path.normpath(os.readlink(link)))
    except OSError:
        return None


def read_current_vgpu_type(vf_address):
    """Read the vGPU type ID assigned to a VF.

    :returns: The type ID, or None when no vGPU is configured (sysfs reports
              ``0``) or the file cannot be read.
    :rtype: Optional[int]
    """
    try:
        type_id = int(_read_sysfs(vf_address, 'nvidia',
                                  'current_vgpu_type').strip())
    except (OSError, ValueError):
        return None
    return type_id or None


def read_creatable_vgpu_types(vf_address):
    try:
        text = _read_sysfs(vf_address, 'nvidia', 'creatable_vgpu_types')
    except OSError:
        return []
    return parse_creatable_vgpu_types(text)


def _load_yaml(path):
    try:
        with open(path) as stream:
            return YAML(typ='safe', pure=True).load(stream)
    except FileNotFoundError:
        return None


def read_catalog(path=None):
    """Read the per-GPU vGPU type catalog.

    ``creatable_vgpu_types`` is empty on a VF once a type is assigned, so the
    ID/name pairs seen while VFs were free are persisted here.

    :returns: PF PCI address mapped to {type ID: profile name}.
    :rtype: Dict[str, Dict[int, str]]
    """
    data = _load_yaml(path or DEFAULT_CATALOG_FILE) or {}
    return {
        pf_address: {int(type_id): name for type_id, name in types.items()}
        for pf_address, types in data.get('gpus', {}).items()
    }
