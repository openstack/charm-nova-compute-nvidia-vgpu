#!/usr/bin/env python3

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


import logging
import json
import os
import shutil

from ruamel.yaml import YAML

from charmhelpers.contrib.openstack.utils import (
    CompareOpenStackReleases,
    get_os_codename_package,
    ows_check_services_running,
)
from charmhelpers.core.hookenv import cached
from charmhelpers.core.host import file_hash, service
from charmhelpers.core.templating import render
from charmhelpers.fetch import apt_cache, apt_install
from charmhelpers.fetch.ubuntu_apt_pkg import version_compare

from ops.model import (
    ActiveStatus,
    BlockedStatus,
    ModelError,
)

import nvidia_utils
import vfio_utils


# First nova-common package carrying the backport of the Nova PCI
# device_spec "managed" tag (Epoxy) to Caracal.
VFIO_MIN_CARACAL_NOVA_VERSION = '3:29.2.0-0ubuntu1.8+vgpu1'


class UnsupportedOpenStackRelease(Exception):
    def __init__(self, release_name):
        super().__init__("Unsupported OpenStack release '{}'".format(
            release_name))


@cached
def is_nvidia_software_to_be_installed(charm_config):
    """Determine whether the NVIDIA vGPU software is to be installed.

    :param charm_config: Juju application configuration object.
    :type charm_config: ops.model.ConfigData
    :returns: True if the software is to be installed and set up on the
              unit.
    :rtype: bool
    """
    return is_nvidia_software_to_be_installed_notcached(charm_config)


def is_nvidia_software_to_be_installed_notcached(charm_config):
    nvidia_gpu_hardware, _ = nvidia_utils.has_nvidia_gpu_hardware()
    return (nvidia_gpu_hardware or
            charm_config.get('force-install-nvidia-vgpu'))


def install_nvidia_software_if_needed(stored, config, resources):
    """Install the NVIDIA software on this unit if relevant.

    :param stored: Unit's stored state.
    :type stored: ops.framework.StoredState
    :param config: Juju application config.
    :type config: ops.model.ConfigData
    :param resources: Juju application resources.
    :type resources: ops.model.Resources
    """
    if is_nvidia_software_to_be_installed(config):
        nvidia_software_path, nvidia_software_hash = (
            _path_and_hash_nvidia_resource(resources))

        if nvidia_software_path is None:
            # No software has been provided as charm resource. We can't
            # install anything. OSBaseCharm.update_status() will be
            # executed later and put the unit in blocked state.
            return

        last_installed_hash = stored.last_installed_resource_hash
        if nvidia_software_hash == last_installed_hash:
            logging.info(
                'NVIDIA vGPU software with hash {} already installed, '
                'skipping'.format(nvidia_software_hash))
            return

        logging.info(
            'Installing NVIDIA vGPU software with hash {}'.format(
                nvidia_software_hash))
        apt_install([nvidia_software_path], fatal=True)
        stored.last_installed_resource_hash = nvidia_software_hash

        # The nouveau driver prevents the nvidia-vgpu-mgr service from
        # starting, thus it needs to be disabled. Unfortunately this requires
        # a reboot. The operator will be notified via a blocked unit status.
        nvidia_utils.disable_nouveau_driver()


def vfio_release_blocker():
    """Check that the installed Nova supports the PCI ``managed`` tag.

    On Caracal an older nova-common only logs a warning, since the
    backport may be shipped under a version the charm does not know.

    :returns: A blocked status message, or None when supported.
    :rtype: Optional[str]
    """
    release_name = _get_current_release()
    release = CompareOpenStackReleases(release_name)
    if release >= 'epoxy':
        return None
    if release == 'caracal':
        version = (apt_cache().dpkg_list(['nova-common'])
                   .get('nova-common', {}).get('version'))
        if not version or version_compare(
                version, VFIO_MIN_CARACAL_NOVA_VERSION) < 0:
            logging.warning(
                'VFIO mode requires nova-common >= %s on Caracal for the '
                'PCI managed tag (installed: %s)',
                VFIO_MIN_CARACAL_NOVA_VERSION, version or 'none')
        return None
    return ('VFIO mode requires OpenStack Caracal with the PCI managed '
            'backport or Epoxy and later (found {})'.format(release_name))


def check_status(config, services):
    """Determine the unit status to be set.

    :param config: Juju application config.
    :type config: ops.model.ConfigData
    :param services: List of services expected to be running.
    :type services: List[str]
    :rtype: ops.model.StatusBase
    """
    installed_versions = nvidia_utils.installed_nvidia_software_versions()
    software_is_installed = len(installed_versions) > 0

    if (is_nvidia_software_to_be_installed(config) and
            not software_is_installed):
        return BlockedStatus("NVIDIA GPU detected, drivers not installed")

    mode = nvidia_utils.detect_vgpu_mode_with_override(
        config.get('vgpu-mode', 'auto'))
    if mode == 'vfio':
        try:
            compute_vfio_desired_state(config)
        except vfio_utils.InvalidGpuProfiles as error:
            return BlockedStatus(str(error))
        except vfio_utils.IntentNotSatisfiable as error:
            return BlockedStatus(
                'gpu-profiles cannot be satisfied: {}'.format(error))

    _, services_not_running_msg = ows_check_services_running(services,
                                                             ports=[])
    software_is_running = services_not_running_msg is None

    if software_is_installed and not software_is_running:
        return BlockedStatus("manual reboot required")

    if mode == 'vfio':
        release_blocker = vfio_release_blocker()
        if release_blocker:
            return BlockedStatus(release_blocker)
        desired_state = vfio_utils.read_desired_state()
        _, pending_vfs = split_live_vf_mappings(desired_state)
        if pending_vfs:
            return BlockedStatus(
                '{} vGPU profile change(s) pending, manual reboot '
                'required'.format(len(pending_vfs)))

    nvidia_gpu_hardware, num_gpus = nvidia_utils.has_nvidia_gpu_hardware()
    unit_status_msg = "{} GPU".format(num_gpus)

    return ActiveStatus('Unit is ready ({})'.format(unit_status_msg))


def set_principal_unit_relation_data(relation_data_to_be_set, config,
                                     services):
    """Pass configuration to a principal unit.

    Branches on detected vGPU mode (mdev or vfio) to generate the
    appropriate Nova config sections.

    :param relation_data_to_be_set: Relation data bag to principal unit.
    :type relation_data_to_be_set: ops.model.RelationData
    :param config: Juju application config.
    :type config: ops.model.ConfigData
    :param services: List of services managed by this unit.
    :type services: List[str]
    :raises: UnsupportedOpenStackRelease
    """
    mode = nvidia_utils.detect_vgpu_mode_with_override(
        config.get('vgpu-mode', 'auto'))

    if mode == 'vfio':
        _set_principal_relation_data_vfio(relation_data_to_be_set, config,
                                          services)
    elif mode == 'mdev':
        _set_principal_relation_data_mdev(relation_data_to_be_set, config,
                                          services)
    else:
        logging.info('No vGPU mode detected, skipping relation data')


def _set_principal_relation_data_mdev(relation_data_to_be_set, config,
                                      services):
    """Set relation data for mdev mode (existing behavior)."""
    vgpu_device_mappings_str = config.get('vgpu-device-mappings')
    if vgpu_device_mappings_str is not None:
        vgpu_device_mappings = YAML().load(vgpu_device_mappings_str)
        if vgpu_device_mappings is None:
            vgpu_device_mappings = {}

        logging.debug('vgpu-device-mappings={}'.format(vgpu_device_mappings))

        nova_conf = json.dumps({
            'nova': {
                '/etc/nova/nova.conf': {
                    'sections': _nova_conf_sections(vgpu_device_mappings)
                }
            }
        })
        relation_data_to_be_set['subordinate_configuration'] = nova_conf
        relation_data_to_be_set['services'] = json.dumps(services)
        relation_data_to_be_set['releases-packages-map'] = json.dumps(
            _releases_packages_map(), sort_keys=True)


def _set_principal_relation_data_vfio(relation_data_to_be_set, config,
                                      services):
    """Set relation data for VFIO mode."""
    release_blocker = vfio_release_blocker()
    if release_blocker:
        logging.error(release_blocker)
        return

    try:
        desired_state = generate_vfio_desired_state(config)
    except (vfio_utils.InvalidGpuProfiles,
            vfio_utils.IntentNotSatisfiable) as error:
        logging.error('VFIO intent cannot be satisfied: %s', error)
        return

    live_vfs, pending_vfs = split_live_vf_mappings(desired_state)
    if pending_vfs:
        logging.info('%d VF profile change(s) will be applied at the '
                     'next reboot', len(pending_vfs))

    nova_conf = json.dumps({
        'nova': {
            '/etc/nova/nova.conf': {
                'sections': _nova_conf_sections_vfio(live_vfs)
            }
        }
    })
    relation_data_to_be_set['subordinate_configuration'] = nova_conf
    relation_data_to_be_set['services'] = json.dumps(services)
    relation_data_to_be_set['releases-packages-map'] = json.dumps(
        _releases_packages_map(), sort_keys=True)
    relation_data_to_be_set['pci-alias-fragments'] = json.dumps(
        _controller_pci_alias_fragments(live_vfs))


def compute_vfio_desired_state(config):
    """Resolve the gpu-profiles intent against the host, without writing.

    :param config: Juju application config
    :type config: ops.model.ConfigData
    :returns: The resolved state and the updated vGPU type catalog.
    :rtype: Tuple[vfio_utils.DesiredState, Dict[str, Dict[int, str]]]
    :raises: vfio_utils.InvalidGpuProfiles, vfio_utils.IntentNotSatisfiable
    """
    intent = vfio_utils.parse_gpu_profiles(config.get('gpu-profiles'))
    pf_addresses = nvidia_utils._find_nvidia_gpu_pci_addresses()

    errors = []
    for pf_address in sorted(set(intent) - set(pf_addresses)):
        message = 'GPU {} from gpu-profiles not found'.format(pf_address)
        if any(spec['policy'] == 'fail' for spec in intent[pf_address]):
            errors.append(message)
        else:
            logging.warning('%s (policy=ignore)', message)
        del intent[pf_address]

    vfs_by_pf = {}
    for pf_address in pf_addresses:
        vfs = vfio_utils.list_vfs(pf_address)
        if not vfs:
            logging.info('No VFs enabled on %s, it is not configured for '
                         'vGPU', pf_address)
            continue
        for vf in vfs:
            vf['current'] = vfio_utils.read_current_vgpu_type(
                vf['pci_address'])
        vfs_by_pf[pf_address] = vfs

    catalog = vfio_utils.update_catalog(vfio_utils.read_catalog(), vfs_by_pf)
    try:
        state = vfio_utils.reconcile_vf_profiles(
            intent, vfs_by_pf, vfio_utils.read_desired_state(), catalog)
    except vfio_utils.IntentNotSatisfiable as error:
        errors.append(str(error))
    if errors:
        raise vfio_utils.IntentNotSatisfiable('; '.join(errors))
    return state, catalog


def generate_vfio_desired_state(config):
    """Resolve the gpu-profiles intent and persist state and catalog.

    :param config: Juju application config
    :type config: ops.model.ConfigData
    :returns: The resolved DesiredState
    :rtype: vfio_utils.DesiredState
    :raises: vfio_utils.InvalidGpuProfiles, vfio_utils.IntentNotSatisfiable
    """
    state, catalog = compute_vfio_desired_state(config)
    vfio_utils.write_catalog(catalog)
    vfio_utils.write_desired_state(state)
    logging.info('Wrote VFIO desired state with %d VFs', len(state.vfs))
    return state


def split_live_vf_mappings(desired_state):
    """Split desired VF assignments by whether sysfs already reflects them.

    VFs recorded with ``remove`` are pending while the vGPU to drop is
    still configured on them.

    :returns: (live, pending) where live is a list of (VfMapping, VF PCI
              address) tuples and pending a list of VfMapping.
    :rtype: Tuple[List[Tuple[vfio_utils.VfMapping, str]],
                  List[vfio_utils.VfMapping]]
    """
    live, pending = [], []
    for vf in desired_state.vfs:
        address = vfio_utils.vf_pci_address(vf.pf_pci_address, vf.index)
        current = (vfio_utils.read_current_vgpu_type(address)
                   if address else None)
        if vf.remove:
            if current == vf.profile['id']:
                pending.append(vf)
        elif current == vf.profile['id']:
            live.append((vf, address))
        else:
            pending.append(vf)
    return live, pending


def _path_and_hash_nvidia_resource(resources):
    """Get path to and hash of software provided as charm resource.

    :param resources: Juju application resources.
    :type resources: ops.model.Resources
    :returns: Pair of path and hash. (None, None) if no charm resource has
              been provided.
    :rtype: Tuple[str, str]
    """
    try:
        nvidia_vgpu_software_path = str(
            resources.fetch('nvidia-vgpu-software'))
    except ModelError:
        return None, None

    return nvidia_vgpu_software_path, file_hash(nvidia_vgpu_software_path)


def _nova_conf_sections(vgpu_device_mappings):
    """Get OpenStack release specific nova.conf sections.

    :param vgpu_device_mappings: vGPU-related settings to be turned into Nova
                                 config bits.
    :type vgpu_device_mappings: Dict[str, List[str]]
    :returns: Dictionary of section names and lists of key/value pairs.
    :rtype: Dict[str, List[Tuple[str, any]]]
    :raises: UnsupportedOpenStackRelease
    """
    current_release_name = _get_current_release()
    current_release = CompareOpenStackReleases(current_release_name)

    if current_release >= 'xena':
        # https://docs.openstack.org/releasenotes/nova/xena.html#deprecation-notes
        result = {
            'devices': [
                ('enabled_mdev_types', ', '.join(vgpu_device_mappings.keys()))
            ]
        }
        for vgpu_type, pci_addresses in vgpu_device_mappings.items():
            result['mdev_{}'.format(vgpu_type)] = [('device_addresses',
                                                    ','.join(pci_addresses))]
        return result

    if current_release >= 'ussuri':
        # https://docs.openstack.org/nova/ussuri/admin/virtual-gpu.html
        result = {
            'devices': [
                ('enabled_vgpu_types', ', '.join(vgpu_device_mappings.keys()))
            ]
        }
        for vgpu_type, pci_addresses in vgpu_device_mappings.items():
            result['vgpu_{}'.format(vgpu_type)] = [('device_addresses',
                                                    ','.join(pci_addresses))]
        return result

    if current_release >= 'queens':
        # https://docs.openstack.org/nova/queens/admin/virtual-gpu.html
        result = {
            'devices': [
                ('enabled_vgpu_types', ', '.join(vgpu_device_mappings.keys()))
            ]
        }
        return result

    raise UnsupportedOpenStackRelease(current_release_name)


def _nova_conf_sections_vfio(live_vfs):
    """Generate Nova [pci] config sections for VFIO mode.

    :param live_vfs: (VfMapping, VF PCI address) tuples, see
                     split_live_vf_mappings().
    :type live_vfs: List[Tuple[vfio_utils.VfMapping, str]]
    :returns: Dict of section names to lists of (key, value) tuples
    :rtype: Dict[str, List[Tuple[str, str]]]
    """
    pci_entries = []
    for vf, address in live_vfs:
        pci_entries.append(('device_spec', json.dumps({
            'vendor_id': '10de',
            'address': address,
            'resource_class': _resource_class(vf.profile['id']),
            'managed': 'no',
        })))
    pci_entries.extend(
        ('alias', alias) for alias in _controller_pci_alias_fragments(
            live_vfs))

    return {
        'pci': pci_entries,
        'libvirt': [
            ('live_migration_permit_post_copy', 'False'),
        ],
    }


def _resource_class(profile_id):
    return 'CUSTOM_NVIDIA_{}'.format(profile_id)


def _controller_pci_alias_fragments(live_vfs):
    """Generate one [pci].alias JSON string per vGPU profile in use.

    :param live_vfs: (VfMapping, VF PCI address) tuples.
    :type live_vfs: List[Tuple[vfio_utils.VfMapping, str]]
    :rtype: List[str]
    """
    profiles = {vf.profile['id']: vf.profile['name'] for vf, _ in live_vfs}
    return [
        json.dumps({
            'device_type': 'type-VF',
            'resource_class': _resource_class(profile_id),
            'name': profiles[profile_id],
        })
        for profile_id in sorted(profiles)
    ]


def _releases_packages_map():
    '''Provide a map of all supported releases and their packages.

    NOTE(lourot): this is a simplified version of a more generic
    implementation:
    https://github.com/openstack/charms.openstack/blob/master/charms_openstack/charm/core.py

    :returns: Map of release, package type and install / purge packages.
        Example:
        {
            'mitaka': {
                'deb': {
                    'install': ['python-ldappool'],
                    'purge': []
                }
            },
            'rocky': {
                'deb': {
                    'install': ['python3-ldap', 'python3-ldappool'],
                    'purge': ['python-ldap', 'python-ldappool']}
            }
        }
    :rtype: Dict[str,Dict[str,List[str]]]
    '''
    return {
        _get_current_release(): {
            'deb': {
                'install': (
                    nvidia_utils.installed_nvidia_software_package_names()),
                'purge': [],
            }
        }
    }


def _get_current_release():
    return get_os_codename_package('nova-common', fatal=False) or 'queens'


def install_mdev_init_workaround(config):
    logging.info("Installing mdev initialisation workaround.")
    shutil.copy('files/initialise_nova_mdevs.sh',
                '/opt/initialise_nova_mdevs.sh')
    os.chmod('/opt/initialise_nova_mdevs.sh', 0o755)

    vgpu_device_mappings = YAML().load(config.get('vgpu-device-mappings') or
                                       "")
    render(
        'remediate_nova_mdevs.py',
        '/opt/remediate-nova-mdevs',
        {'mdev_types': vgpu_device_mappings},
        perms=0o755)

    render(
        'systemd-mdev-workaround.service',
        '/etc/systemd/system/systemd-mdev-workaround.service',
        {},
        perms=0o644)
    service('enable', 'systemd-mdev-workaround')
    # enable but not start since this needs to be done once on boot
