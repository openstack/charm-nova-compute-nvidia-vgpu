#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
import os
import socket
from functools import cached_property
from time import sleep
from xml.dom import minidom

import libvirt
import nova.conf
from nova.utils import get_sdk_adapter
from nova.pci.utils import get_pci_address

LOG = logging.getLogger(__name__)
CONF = nova.conf.CONF

NOVA_CONF = '/etc/nova/nova.conf'
# Dictionary of mdev types and and address mappings
MDEV_TYPES = {{ mdev_types }}  # noqa pylint: disable=unhashable-member,undefined-variable


class PlacementError(Exception):
    """ Raised when as error occurs in the PlacementHelper. """


class RemediationFailedError(Exception):
    """ Raised when as error occurs during mdev remediation. """


class PlacementHelper():
    """
    Helper for Placement operations.
    """
    DRIVER_TRAIT_MAPPING = {'nvidia-610': 'CUSTOM_VGPU_PLACEMENT'}

    def __init__(self):
        self.fqdn = socket.getfqdn()
        self.client = self._get_sdk_adapter_helper("placement")
        if self.client is None:
            raise PlacementError("failed to get placement client")

    @staticmethod
    def _get_sdk_adapter_helper(service_type):
        count = 1
        while True:
            LOG.info("fetching %s sdk adapter (attempt=%s)", service_type,
                     count)
            try:
                return get_sdk_adapter(service_type)
            except Exception as e:
                count += 1
                if count > 30:
                    LOG.error(e)
                    return None

                LOG.warning("failed to get %s sdk adapter - trying again",
                            service_type)
                sleep(0.5)

    @cached_property
    def local_compute_rps(self):
        LOG.info("fetching resources providers for host %s", self.fqdn)
        rps = self.client.get("/resource_providers")
        if rps.status_code != 200:
            raise PlacementError(f"failed to get rps: {rps}")

        prefix = f'{self.fqdn}_pci_'
        data = rps.json()
        result = []
        for rp in data['resource_providers']:
            if rp['name'].startswith(prefix):
                result.append(rp)

        LOG.info("found %s resources providers for host %s", len(result),
                 self.fqdn)
        return result

    @cached_property
    def traits(self):
        resp = self.client.get("/traits", microversion='1.6')
        if resp.status_code != 200:
            raise PlacementError(f"failed to get traits: {resp}")

        _traits = resp.json()
        if not _traits:
            raise PlacementError("no traits identified from the placement api")

        for trait in self.DRIVER_TRAIT_MAPPING.values():
            if trait not in _traits['traits']:
                raise PlacementError(f"trait {trait} not found in placement "
                                     "traits")

        return _traits

    def get_traits_for_rp(self, uuid):
        resp = self.client.get(f"/resource_providers/{uuid}/traits",
                               microversion='1.6')
        if resp.status_code != 200:
            raise PlacementError(f"failed to traits for rp {uuid}: {resp}")

        return resp.json()

    def update_traits_on_rp(self, uuid, generation, new_traits):
        data = {
            'resource_provider_generation': generation,
            'traits': new_traits,
        }
        resp = self.client.put(f"/resource_providers/{uuid}/traits",
                               json=data, microversion='1.6')
        if resp.status_code != 200:
            raise PlacementError(f"failed to update traits for rp {uuid}: "
                                 f"{resp}")

        LOG.info("updated traits on RP %s to %s", uuid, new_traits)

    def get_vgpu_rp_name(self, uuid):
        allocations = self.client.get(f"/allocations/{uuid}")
        if allocations.status_code != 200:
            raise PlacementError(f"failed to get allocation for uuid {uuid}: "
                                 f"{allocations} "
                                 f"(return_code={allocations.status_code})")

        data = allocations.json()
        if not data.get('allocations'):
            raise PlacementError(f"no allocations found for uuid {uuid}, does "
                                 "instance exist?")

        for rp, rpdata in data['allocations'].items():
            if rpdata.get('resources') is None:
                continue

            for r, v in rpdata['resources'].items():
                if r != 'VGPU' or v != 1:
                    continue

                return self.get_rp_name(rp)

        raise PlacementError(f"no resource provider found for domain {uuid}")

    def get_rp_name(self, uuid):
        rp = self.client.get(f"/resource_providers/{uuid}")
        if rp.status_code != 200:
            raise PlacementError(f"failed to get rp for uuid {uuid} "
                                 f"(return_code={rp.status_code})")

        rp_name = rp.json().get('name')
        if not rp_name:
            raise PlacementError("failed to find resource provider name for "
                                 f"domain {uuid}")

        return rp_name

    @staticmethod
    def get_pci_addr_from_rp_name(rpname):
        addr = rpname.split('_pci_')[1]
        pci_id_parts = addr.split('_')
        return get_pci_address(*pci_id_parts)

    def update_gpu_traits(self, rpname, rpuuid, dry_run=False):
        LOG.info("updating gpu traits for resource provider %s", rpuuid)
        traits = self.get_traits_for_rp(rpuuid)
        if traits is None:
            LOG.info("no traits found for resource provider %s "
                     "- skipping update", rpuuid)
            return

        pci_address = self.get_pci_addr_from_rp_name(rpname)
        driver = find_driver_type_from_pci_address(pci_address)
        if driver is None:
            if len(traits['traits']) > 0:
                LOG.warning("rp %s for %s has traits %s but should be empty",
                            rpuuid, pci_address, traits['traits'])

            return

        if driver not in self.DRIVER_TRAIT_MAPPING:
            LOG.error("failed to map driver '%s' to a trait for PCI "
                      "address %s", driver, pci_address)
            return

        expected_traits = [self.DRIVER_TRAIT_MAPPING[driver]]
        if expected_traits != traits['traits']:
            if dry_run:
                LOG.warning("rp %s for %s is mapped to driver %s but "
                            "traits is %s not %s - skipping update since "
                            "dry_run is True",
                            rpuuid, pci_address, driver,
                            traits['traits'], expected_traits)
                return

            self.update_traits_on_rp(rpuuid,
                                     traits['resource_provider_generation'],
                                     expected_traits)


class LibvirtHelper():
    """
    Helper for Libvirt operations.
    """

    @cached_property
    def domains(self):
        try:
            conn = libvirt.openReadOnly('qemu:///system')
            return conn.listAllDomains(0)
        finally:
            conn.close()

        return None

    @staticmethod
    def get_domain_hostdevs(domain):
        raw_xml = domain.XMLDesc()
        xml = minidom.parseString(raw_xml)
        return xml.getElementsByTagName("hostdev")


def _mdev_exists(uuid):
    path = os.path.join("/sys/bus/mdev/devices", uuid)
    return os.path.exists(path)


def _create_mdev(pci_addr, driver_type, uuid, dry_run=False):
    path = os.path.join('/sys/bus/pci/devices', pci_addr,
                        'mdev_supported_types', driver_type, 'create')
    LOG.info("creating mdev entry at path: %s", path)
    if dry_run:
        LOG.info("skipping since dry_run is True")
        return

    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(uuid)
    except Exception as e:
        raise RemediationFailedError(f"failed to create mdev {uuid} at "  # noqa pylint: disable=raise-missing-from
                                     f"{pci_addr} with type {driver_type}: "
                                     f"{e}")

    LOG.info("created mdev %s at %s with type %s", uuid, pci_addr, driver_type)


def find_driver_type_from_pci_address(pci_addr):
    for driver_type, addresses in MDEV_TYPES.items():  # noqa pylint: disable=no-member,undefined-variable
        if pci_addr in addresses:
            return driver_type

    return None


def _remediate_hostdev(pm, domain_uuid, hostdev, dry_run=False):
    sources = hostdev.getElementsByTagName("source")
    if len(sources) <= 0:
        raise RemediationFailedError("found no source elements in hostdev: "
                                     f"{hostdev.toxml()}")

    if len(sources) > 1:
        raise RemediationFailedError(f"found more than one ({len(sources)}) "
                                     "source elements in hostdev: "
                                     f"{hostdev.toxml()}")

    source = sources[0]
    addresses = source.getElementsByTagName("address")
    if len(addresses) != 1:
        raise RemediationFailedError(f"expected to find only one address "
                                     f"(({len(addresses)})) in source for "
                                     f"hostdev: {hostdev.toxml()}")

    mdev_uuid = addresses[0].getAttribute("uuid")
    if _mdev_exists(mdev_uuid):
        LOG.info("hostdev mdev device %s already exists - no action needed",
                 mdev_uuid)
        return

    try:
        rp_name = pm.get_vgpu_rp_name(domain_uuid)
    except PlacementError as exc:
        raise RemediationFailedError from exc

    if '_pci_' not in rp_name:
        raise RemediationFailedError("failed to find _pci_ in provider name "
                                     f"'{rp_name}'")

    pci_address = pm.get_pci_addr_from_rp_name(rp_name)
    driver_type = find_driver_type_from_pci_address(pci_address)
    if not driver_type:
        raise RemediationFailedError("failed to find driver type for "
                                     f"pci_address {pci_address}")

    _create_mdev(pci_address, driver_type, mdev_uuid, dry_run)


def main(dry_run=False):
    logging.basicConfig(level=logging.INFO)
    LOG.info("starting Nova mdev remediation (dry_run=%s)", dry_run)
    LOG.info("loading Nova config from %s", NOVA_CONF)
    CONF(default_config_files=[NOVA_CONF])

    lm = LibvirtHelper()
    pm = PlacementHelper()

    if len(lm.domains) == 0:
        LOG.info("no domains found in libvirt - exiting")
        return

    LOG.info("%s domains found in libvirt", len(lm.domains))
    failed = False
    for domain in lm.domains:
        uuid, name = domain.UUIDString(), domain.name()
        hostdevs = lm.get_domain_hostdevs(domain)
        if len(hostdevs) <= 0:
            LOG.info("domain %s (%s) has no hostdevs - skipping", uuid, name)
            continue

        LOG.info("domain %s (%s) has %d hostdev(s) - starting remediation",
                 uuid, name, len(hostdevs))
        for hostdev in hostdevs:
            try:
                _remediate_hostdev(pm, uuid, hostdev, dry_run)
            except RemediationFailedError as exc:
                LOG.error(exc)
                failed = True

    if not pm.local_compute_rps:
        return

    for rp in pm.local_compute_rps:
        pm.update_gpu_traits(rp['name'], rp['uuid'], dry_run)

    if failed:
        raise PlacementError("failed to update one or more placement traits")


if __name__ == '__main__':
    main(os.environ.get('MDEV_INIT_DRY_RUN') == 'True')
