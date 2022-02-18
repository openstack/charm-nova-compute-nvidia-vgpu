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

from charmhelpers.core.hookenv import cached
from charmhelpers.core.host import file_hash
from charmhelpers.fetch import apt_install

from ops.model import (
    ActiveStatus,
    BlockedStatus,
    ModelError,
)

import nvidia_utils


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
    return (nvidia_utils.has_nvidia_gpu_hardware() or
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


def check_status(config):
    """Determine the unit status to be set.

    :param config: Juju application config.
    :type config: ops.model.ConfigData
    :rtype: ops.model.StatusBase
    """
    unit_status_msg = ('no ' if not nvidia_utils.has_nvidia_gpu_hardware()
                       else '') + 'NVIDIA GPU found; '

    installed_versions = nvidia_utils.installed_nvidia_software_versions()
    if len(installed_versions) > 0:
        unit_status_msg += 'installed NVIDIA software: '
        unit_status_msg += ', '.join(installed_versions)
    else:
        unit_status_msg += 'no NVIDIA software installed'

    if (is_nvidia_software_to_be_installed(config) and
            len(installed_versions) == 0):
        return BlockedStatus(unit_status_msg)

    return ActiveStatus('Unit is ready: ' + unit_status_msg)


def set_relation_data(relation_data, key, value):
    """Mockable setter.

    Workaround for https://github.com/canonical/operator/issues/703
    Used in unit test
    TestNovaComputeNvidiaVgpuCharm.test_nova_vgpu_relation_joined

    :param relation_data: Relation data bag.
    :type relation_data: ops.model.RelationData
    """
    relation_data[key] = value


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
