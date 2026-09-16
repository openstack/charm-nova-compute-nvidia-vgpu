#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0


import logging
import os
import re
from dataclasses import dataclass, field
from typing import List

from ruamel.yaml import YAML, YAMLError


DEFAULT_STATE_FILE = '/etc/nvidia-vgpu-state.yaml'
DEFAULT_CATALOG_FILE = '/etc/nvidia-vgpu-catalog.yaml'
PROFILE_POLICIES = ('ignore', 'fail')

SYS_PCI_BASE = '/sys/bus/pci/devices'

# Lines of the vendor-specific VFIO ``creatable_vgpu_types`` file. NVIDIA
# documents the ``<name>   <id>`` layout, e.g. ``NVIDIA A40-4Q   560``; the
# ``<id> : <name>`` layout is accepted too since some driver branches use it.
_TYPE_NAME_FIRST_RE = re.compile(r'^\s*(\S.*?)\s+(\d+)\s*$')
_TYPE_ID_FIRST_RE = re.compile(r'^\s*(\d+)\s*:\s*(\S.*?)\s*$')
_VIRTFN_RE = re.compile(r'^virtfn(\d+)$')
_PCI_ADDRESS_RE = re.compile(
    r'^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$')
_FRAMEBUFFER_RE = re.compile(r'-(\d+)[A-Za-z]+$')


class IntentNotSatisfiable(Exception):
    """Raised when operator intent cannot be met with policy='fail'."""


class InvalidGpuProfiles(ValueError):
    """Raised when the gpu-profiles config option is malformed."""


@dataclass
class VfMapping:
    """VF-to-profile assignment.

    ``remove`` marks a surplus VF whose vGPU must not be recreated by the
    boot-time setup after the operator reduced the profile count.
    """
    pf_pci_address: str
    index: int
    profile: dict
    remove: bool = False


@dataclass
class DesiredState:
    vfs: List[VfMapping] = field(default_factory=list)


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


def _dump_yaml(data, path):
    temporary_path = '{}.tmp'.format(path)
    with open(temporary_path, 'w') as stream:
        YAML(typ='safe', pure=True).dump(data, stream)
    os.replace(temporary_path, path)


def _load_yaml(path):
    try:
        with open(path) as stream:
            return YAML(typ='safe', pure=True).load(stream)
    except FileNotFoundError:
        return None


def parse_gpu_profiles(raw):
    """Parse and validate the ``gpu-profiles`` config option.

    :param raw: Raw option value (YAML/JSON string, may be empty or None).
    :type raw: Optional[str]
    Nova models all VFs of a GPU as one Placement resource provider with a
    single resource class, so each GPU can only be given one vGPU type.

    :returns: PF PCI address mapped to a list of at most one dict with
              'type' (normalized name), 'count' and 'policy' keys.
    :rtype: Dict[str, List[dict]]
    :raises: InvalidGpuProfiles
    """
    if raw is None or not str(raw).strip():
        return {}
    try:
        data = YAML(typ='safe', pure=True).load(raw)
    except YAMLError as error:
        raise InvalidGpuProfiles(
            'gpu-profiles is not valid YAML: {}'.format(error))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise InvalidGpuProfiles(
            'gpu-profiles must map GPU PCI addresses to lists of profiles')

    gpu_profiles = {}
    for address, specs in data.items():
        address = str(address).strip().lower()
        if not _PCI_ADDRESS_RE.match(address):
            raise InvalidGpuProfiles(
                'gpu-profiles: invalid GPU PCI address {!r}'.format(address))
        if not isinstance(specs, list):
            raise InvalidGpuProfiles(
                'gpu-profiles: {} must map to a list of profiles'.format(
                    address))
        profiles = [_parse_profile_spec(address, spec) for spec in specs]
        if len(profiles) > 1:
            raise InvalidGpuProfiles(
                'gpu-profiles: {} lists {}; Nova supports a single vGPU type '
                'per GPU'.format(address, ', '.join(
                    profile['type'] for profile in profiles)))
        gpu_profiles[address] = profiles
    return gpu_profiles


def _parse_profile_spec(address, spec):
    if not isinstance(spec, dict):
        raise InvalidGpuProfiles(
            'gpu-profiles: {} entries must be mappings'.format(address))
    unknown_keys = set(spec) - {'type', 'count', 'policy'}
    if unknown_keys:
        raise InvalidGpuProfiles(
            'gpu-profiles: {} has unknown keys {}'.format(
                address, sorted(unknown_keys)))
    name = spec.get('type')
    if not isinstance(name, str) or not name.strip():
        raise InvalidGpuProfiles(
            'gpu-profiles: {} entry is missing a profile type'.format(address))
    count = spec.get('count')
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise InvalidGpuProfiles(
            'gpu-profiles: {} {} needs a positive integer count'.format(
                address, name))
    policy = spec.get('policy', 'ignore')
    if policy not in PROFILE_POLICIES:
        raise InvalidGpuProfiles(
            'gpu-profiles: {} {} has invalid policy {!r}'.format(
                address, name, policy))
    return {'type': normalize_profile_name(name), 'count': count,
            'policy': policy}


def framebuffer_size(profile_name):
    """Frame buffer size (GB) encoded in a profile name, e.g. A40-4Q -> 4."""
    match = _FRAMEBUFFER_RE.search(profile_name)
    return int(match.group(1)) if match else 0


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


def write_catalog(catalog, path=None):
    _dump_yaml({'gpus': {
        pf_address: dict(sorted(types.items()))
        for pf_address, types in sorted(catalog.items())
    }}, path or DEFAULT_CATALOG_FILE)


def update_catalog(catalog, vfs_by_pf):
    """Merge the vGPU types currently creatable on each VF into a catalog.

    :param catalog: Existing catalog, see read_catalog().
    :param vfs_by_pf: PF PCI address mapped to the VFs from list_vfs().
    :returns: New catalog; entries are only ever added.
    """
    updated = {pf_address: dict(types) for pf_address, types in
               catalog.items()}
    for pf_address, vfs in vfs_by_pf.items():
        pf_types = updated.setdefault(pf_address, {})
        for vf in vfs:
            for vgpu_type in read_creatable_vgpu_types(vf['pci_address']):
                pf_types[vgpu_type['id']] = vgpu_type['name']
    return updated


def read_desired_state(path=None):
    """Read persisted VF profile assignments.

    Entries recorded with ``remove`` are surplus VFs whose vGPUs must not
    be recreated at boot. Returns an empty state if the file is absent.

    :param path: Desired-state file path, DEFAULT_STATE_FILE if None.
    :type path: Optional[str]
    :returns: Desired VF-to-profile state.
    :rtype: DesiredState
    """
    data = _load_yaml(path or DEFAULT_STATE_FILE) or {}
    return DesiredState(vfs=[
        VfMapping(
            pf_pci_address=entry['pf_pci_address'],
            index=int(entry['index']),
            profile={'id': int(entry['profile']['id']),
                     'name': entry['profile']['name']},
            remove=bool(entry.get('remove', False)),
        )
        for entry in data.get('vfs') or []
    ])


def write_desired_state(state, path=None):
    """Write desired VF profile assignments to YAML.

    :param state: Desired VF-to-profile state.
    :type state: DesiredState
    :param path: Desired-state file path, DEFAULT_STATE_FILE if None.
    :type path: Optional[str]
    """
    entries = []
    for vf in state.vfs:
        entry = {
            'pf_pci_address': vf.pf_pci_address,
            'index': vf.index,
            'profile': {'id': vf.profile['id'], 'name': vf.profile['name']},
        }
        if vf.remove:
            entry['remove'] = True
        entries.append(entry)
    _dump_yaml({'vfs': entries}, path or DEFAULT_STATE_FILE)


def reconcile_vf_profiles(intent, vfs_by_pf, existing_state, catalog):
    """Build desired state from operator intent with sticky reconciliation.

    For each GPU, VFs keep the profile they currently have (or, when free,
    the one last recorded in ``existing_state``) while that profile is still
    part of the intent and its requested count is not covered yet. VFs
    holding a requested profile beyond the count are not touched: they are
    recorded with ``remove`` set so the boot-time setup does not recreate
    their vGPUs. The remaining requested vGPUs go to the remaining VFs in
    virtfn order as long as the GPU frame buffer (the largest profile in
    the catalog) is not exceeded.

    GPUs whose VFs are not visible, or whose vGPU types are unknown, keep
    the assignments recorded in ``existing_state``; with policy=fail this
    counts as an unmet requirement.

    :param intent: Validated output of parse_gpu_profiles().
    :type intent: Dict[str, List[dict]]
    :param vfs_by_pf: PF PCI address mapped to lists of dicts with 'index'
                      and 'current' (assigned type ID or None) keys.
    :type vfs_by_pf: Dict[str, List[dict]]
    :param existing_state: Last persisted desired state.
    :type existing_state: DesiredState
    :param catalog: See read_catalog().
    :type catalog: Dict[str, Dict[int, str]]
    :returns: New desired VF-to-profile state.
    :rtype: DesiredState
    :raises IntentNotSatisfiable: If a policy=fail requirement is unmet.
    """
    errors = []
    result = []
    for pf_address in sorted(intent):
        pf_catalog = catalog.get(pf_address, {})
        if pf_address not in vfs_by_pf or not pf_catalog:
            if pf_address not in vfs_by_pf:
                message = ('no VFs of {} are available (a reboot may be '
                           'required to enable them)').format(pf_address)
            else:
                message = 'vGPU types of {} are unknown'.format(pf_address)
            for spec in intent[pf_address]:
                _unmet(errors, spec, message)
            logging.info('VFs or vGPU types of %s not available, keeping '
                         'recorded assignments', pf_address)
            result.extend(vf for vf in existing_state.vfs
                          if vf.pf_pci_address == pf_address)
            continue
        result.extend(_reconcile_pf(
            pf_address, intent[pf_address], vfs_by_pf[pf_address],
            existing_state, pf_catalog, errors))

    if errors:
        raise IntentNotSatisfiable('; '.join(errors))
    return DesiredState(vfs=sorted(
        result, key=lambda vf: (vf.pf_pci_address, vf.index)))


def _reconcile_pf(pf_address, specs, vfs, existing_state, pf_catalog,
                  errors):
    name_to_id = {name: type_id for type_id, name in pf_catalog.items()}
    capacity = max(framebuffer_size(name) for name in pf_catalog.values())
    existing = {vf.index: vf.profile['id'] for vf in existing_state.vfs
                if vf.pf_pci_address == pf_address}

    requirements = []
    for spec in specs:
        type_id = name_to_id.get(spec['type'])
        if type_id is None:
            _unmet(errors, spec, '{} is not a vGPU type of {}'.format(
                spec['type'], pf_address))
            continue
        requirements.extend([(type_id, spec)] * spec['count'])
    intent_ids = {type_id for type_id, _ in requirements}

    assigned = {}
    removals = {}
    for vf in vfs:
        type_id = vf['current'] or existing.get(vf['index'])
        match = next((position for position, (req_id, _) in
                      enumerate(requirements) if req_id == type_id), None)
        if match is not None:
            requirements.pop(match)
            assigned[vf['index']] = type_id
        elif vf['current'] in intent_ids:
            # The profile is still requested but the count is already
            # covered: leave the VF alone and make sure its vGPU is not
            # recreated at the next boot.
            removals[vf['index']] = vf['current']

    used = sum(framebuffer_size(pf_catalog[type_id])
               for type_id in assigned.values())
    free_vfs = [vf['index'] for vf in vfs if vf['index'] not in assigned]
    for type_id, spec in requirements:
        size = framebuffer_size(spec['type'])
        if not free_vfs:
            _unmet(errors, spec, 'no free VF left on {} for {}'.format(
                pf_address, spec['type']))
        elif capacity and used + size > capacity:
            _unmet(errors, spec, 'not enough frame buffer on {} for '
                   '{}'.format(pf_address, spec['type']))
        else:
            index = free_vfs.pop(0)
            assigned[index] = type_id
            removals.pop(index, None)
            used += size

    mappings = [VfMapping(pf_pci_address=pf_address, index=index,
                          profile={'id': type_id,
                                   'name': pf_catalog[type_id]})
                for index, type_id in assigned.items()]
    mappings.extend(
        VfMapping(pf_pci_address=pf_address, index=index,
                  profile={'id': type_id, 'name': pf_catalog[type_id]},
                  remove=True)
        for index, type_id in sorted(removals.items()))
    return mappings


def _unmet(errors, spec, message):
    if spec['policy'] == 'fail':
        errors.append(message)
    else:
        logging.warning('%s (policy=ignore)', message)
