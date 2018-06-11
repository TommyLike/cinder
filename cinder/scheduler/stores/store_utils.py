# Copyright (c) 2018 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


from oslo_config import cfg
from oslo_log import log as logging

from cinder import context
from cinder import utils
from cinder.volume import utils as vol_utils

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

REQUIRED_KEYS = frozenset([
    'pool_name',
    'total_capacity_gb',
    'free_capacity_gb',
    'allocated_capacity_gb',
    'provisioned_capacity_gb',
    'thin_provisioning_support',
    'thick_provisioning_support',
    'max_over_subscription_ratio',
    'reserved_percentage'])


def get_updated_pools(self, old_capa, new_capa):
    # Judge if the capabilities should be reported.

    new_pools = new_capa.get('pools', [])
    if not new_pools:
        return []

    if isinstance(new_pools, list):
        # If the volume_stats is not well prepared, don't notify.
        if not all(
                REQUIRED_KEYS.issubset(pool) for pool in new_pools):
            return []
    else:
        LOG.debug("The reported capabilities are not well structured...")
        return []

    old_pools = old_capa.get('pools', [])
    if not old_pools:
        return new_pools

    updated_pools = []

    newpools = {}
    oldpools = {}
    for new_pool in new_pools:
        newpools[new_pool['pool_name']] = new_pool

    for old_pool in old_pools:
        oldpools[old_pool['pool_name']] = old_pool

    for key in newpools.keys():
        if key in oldpools.keys():
            for k in self.REQUIRED_KEYS:
                if newpools[key][k] != oldpools[key][k]:
                    updated_pools.append(newpools[key])
                    break
        else:
            updated_pools.append(newpools[key])

    return updated_pools


def get_usage_and_notify(self, capa_new, updated_pools, host, timestamp):
    ctxt = context.get_admin_context()
    usage = self._get_usage(capa_new, updated_pools, host, timestamp)
    self._notify_capacity_usage(ctxt, usage)


def _get_usage(self, capa_new, updated_pools, host, timestamp):
    pools = capa_new.get('pools')
    usage = []
    if pools and isinstance(pools, list):
        backend_usage = dict(type='backend',
                             name_to_id=host,
                             total=0,
                             free=0,
                             allocated=0,
                             provisioned=0,
                             virtual_free=0,
                             reported_at=timestamp)

        # Process the usage.
        for pool in pools:
            pool_usage = self._get_pool_usage(pool, host, timestamp)
            if pool_usage:
                backend_usage["total"] += pool_usage["total"]
                backend_usage["free"] += pool_usage["free"]
                backend_usage["allocated"] += pool_usage["allocated"]
                backend_usage["provisioned"] += pool_usage["provisioned"]
                backend_usage["virtual_free"] += pool_usage["virtual_free"]
            # Only the updated pool is reported.
            if pool in updated_pools:
                usage.append(pool_usage)
        usage.append(backend_usage)
    return usage


def _get_pool_usage(self, pool, host, timestamp):
    total = pool["total_capacity_gb"]
    free = pool["free_capacity_gb"]

    unknowns = ["unknown", "infinite", None]
    if (total in unknowns) or (free in unknowns):
        return {}

    allocated = pool["allocated_capacity_gb"]
    provisioned = pool["provisioned_capacity_gb"]
    reserved = pool["reserved_percentage"]
    ratio = utils.calculate_max_over_subscription_ratio(
        pool, CONF.max_over_subscription_ratio)
    support = pool["thin_provisioning_support"]

    virtual_free = utils.calculate_virtual_free_capacity(
        total,
        free,
        provisioned,
        support,
        ratio,
        reserved,
        support)

    pool_usage = dict(
        type='pool',
        name_to_id='#'.join([host, pool['pool_name']]),
        total=float(total),
        free=float(free),
        allocated=float(allocated),
        provisioned=float(provisioned),
        virtual_free=float(virtual_free),
        reported_at=timestamp)

    return pool_usage


def _notify_capacity_usage(self, context, usage):
    if usage:
        for u in usage:
            vol_utils.notify_about_capacity_usage(
                context, u, u['type'], None, None)
    LOG.debug("Publish storage capacity: %s.", usage)
