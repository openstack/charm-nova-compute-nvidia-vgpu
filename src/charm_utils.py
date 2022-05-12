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

from ruamel.yaml import YAML

from charmhelpers.contrib.openstack.utils import (
    CompareOpenStackReleases,
    get_os_codename_package,
    ows_check_services_running,
)
from charmhelpers.core.hookenv import cached
from charmhelpers.core.host import file_hash
from charmhelpers.fetch import apt_install

from ops.model import (
    ActiveStatus,
    BlockedStatus,
    ModelError,
)

import nvidia_utils


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

    _, services_not_running_msg = ows_check_services_running(services,
                                                             ports=[])
    software_is_running = services_not_running_msg is None

    if software_is_installed and not software_is_running:
        return BlockedStatus("manual reboot required")

    nvidia_gpu_hardware, num_gpus = nvidia_utils.has_nvidia_gpu_hardware()
    unit_status_msg = "{} GPU".format(num_gpus)

    return ActiveStatus('Unit is ready ({})'.format(unit_status_msg))


def set_principal_unit_relation_data(relation_data_to_be_set, config,
                                     services):
    """Pass configuration to a principal unit.

    :param relation_data_to_be_set: Relation data bag to principal unit.
    :type relation_data_to_be_set: ops.model.RelationData
    :param config: Juju application config.
    :type config: ops.model.ConfigData
    :param services: List of services managed by this unit.
    :type services: List[str]
    :raises: UnsupportedOpenStackRelease
    """
    vgpu_device_mappings_str = config.get('vgpu-device-mappings')
    if vgpu_device_mappings_str is not None:
        vgpu_device_mappings = YAML().load(vgpu_device_mappings_str)

        if vgpu_device_mappings is None:  # happens when passing an empty str
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
        logging.debug(
            'relation data to principal unit set to '
            'subordinate_configuration={}'.format(nova_conf))

        relation_data_to_be_set['services'] = json.dumps(services)
        relation_data_to_be_set['releases-packages-map'] = json.dumps(
            _releases_packages_map(), sort_keys=True)


def _path_and_hash_nvidia_resource(resources):
    """Get path to and hash of software provided as charm resource.

    :param resources: Juju application resources.
    :type resources: ops.model.Resources
    :returns: Pair of path and hash. (None, None) if no charm resource has
              been provided.
    :rtype: Tuple[PosixPath, str]
    """
    try:
        nvidia_vgpu_software_path = resources.fetch('nvidia-vgpu-software')
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
