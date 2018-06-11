# Copyright (c) 2011 OpenStack Foundation
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

"""
Manage backends in the current zone.
"""

import collections

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import importutils
from oslo_utils import strutils
from oslo_utils import timeutils

from cinder.common import constants
from cinder import context as cinder_context
from cinder import exception
from cinder import objects
from cinder.scheduler import filters
from cinder import utils
from cinder.volume import utils as vol_utils
from cinder.volume import volume_types


# FIXME: This file should be renamed to backend_manager, we should also rename
# HostManager class, and scheduler_host_manager option, and also the weight
# classes, and add code to maintain backward compatibility.


host_manager_opts = [
    cfg.ListOpt('scheduler_default_filters',
                default=[
                    'AvailabilityZoneFilter',
                    'CapacityFilter',
                    'CapabilitiesFilter'
                ],
                help='Which filter class names to use for filtering hosts '
                     'when not specified in the request.'),
    cfg.ListOpt('scheduler_default_weighers',
                default=[
                    'CapacityWeigher'
                ],
                help='Which weigher class names to use for weighing hosts.'),
    cfg.StrOpt('scheduler_weight_handler',
               default='cinder.scheduler.weights.OrderedHostWeightHandler',
               help='Which handler to use for selecting the host/pool '
                    'after weighing'),
    cfg.StrOpt('backend_store_driver',
               default='cinder.scheduler.stores.memory.MemoryStoreDriver',
               help='Default store driver to store scheduler data.'),
    cfg.StrOpt('backend_store_resource_ttl',
               default=60,
               help='How long in seconds will the backend status will be '
                    'maintained in host manager store.')
]

CONF = cfg.CONF
CONF.register_opts(host_manager_opts)
CONF.import_opt('scheduler_driver', 'cinder.scheduler.manager')
CONF.import_opt('max_over_subscription_ratio', 'cinder.volume.driver')

LOG = logging.getLogger(__name__)


class ReadOnlyDict(collections.Mapping):
    """A read-only dict."""
    def __init__(self, source=None):
        if source is not None:
            self.data = dict(source)
        else:
            self.data = {}

    def __getitem__(self, key):
        return self.data[key]

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self.data)


class BackendState(object):
    """Mutable and immutable information tracked for a volume backend."""

    def __init__(self, host, cluster_name, capabilities=None, service=None):
        # NOTE(geguileo): We have a circular dependency between BackendState
        # and PoolState and we resolve it with an instance attribute instead
        # of a class attribute that we would assign after the PoolState
        # declaration because this way we avoid splitting the code.
        self.pool_state_cls = PoolState

        self.capabilities = None
        self.service = None
        self.host = host
        self.cluster_name = cluster_name
        self.update_capabilities(capabilities, service)

        self.volume_backend_name = None
        self.vendor_name = None
        self.driver_version = 0
        self.storage_protocol = None
        self.QoS_support = False
        # Mutable available resources.
        # These will change as resources are virtually "consumed".
        self.total_capacity_gb = 0
        # capacity has been allocated in cinder POV, which should be
        # sum(vol['size'] for vol in vols_on_hosts)
        self.allocated_capacity_gb = 0
        self.free_capacity_gb = None
        self.reserved_percentage = 0
        # The apparent allocated space indicating how much capacity
        # has been provisioned. This could be the sum of sizes of
        # all volumes on a backend, which could be greater than or
        # equal to the allocated_capacity_gb.
        self.provisioned_capacity_gb = 0
        self.max_over_subscription_ratio = 1.0
        self.thin_provisioning_support = False
        self.thick_provisioning_support = False
        # Does this backend support attaching a volume to more than
        # once host/instance?
        self.multiattach = False

        # PoolState for all pools
        self.pools = {}

        self.updated = None

    @property
    def backend_id(self):
        return self.cluster_name or self.host

    def update_capabilities(self, capabilities=None, service=None):
        # Read-only capability dicts

        if capabilities is None:
            capabilities = {}
        self.capabilities = ReadOnlyDict(capabilities)
        if service is None:
            service = {}
        self.service = ReadOnlyDict(service)

    def update_from_volume_capability(self, capability, service=None):
        """Update information about a host from its volume_node info.

        'capability' is the status info reported by volume backend, a typical
        capability looks like this:

        .. code-block:: python

         {
          capability = {
              'volume_backend_name': 'Local iSCSI', #
              'vendor_name': 'OpenStack',           #  backend level
              'driver_version': '1.0',              #  mandatory/fixed
              'storage_protocol': 'iSCSI',          #  stats&capabilities

              'active_volumes': 10,                 #
              'IOPS_provisioned': 30000,            #  optional custom
              'fancy_capability_1': 'eat',          #  stats & capabilities
              'fancy_capability_2': 'drink',        #

              'pools': [
                  {'pool_name': '1st pool',         #
                   'total_capacity_gb': 500,        #  mandatory stats for
                   'free_capacity_gb': 230,         #  pools
                   'allocated_capacity_gb': 270,    #
                   'QoS_support': 'False',          #
                   'reserved_percentage': 0,        #

                   'dying_disks': 100,              #
                   'super_hero_1': 'spider-man',    #  optional custom
                   'super_hero_2': 'flash',         #  stats & capabilities
                   'super_hero_3': 'neoncat'        #
                  },
                  {'pool_name': '2nd pool',
                   'total_capacity_gb': 1024,
                   'free_capacity_gb': 1024,
                   'allocated_capacity_gb': 0,
                   'QoS_support': 'False',
                   'reserved_percentage': 0,

                   'dying_disks': 200,
                   'super_hero_1': 'superman',
                   'super_hero_2': ' ',
                   'super_hero_2': 'Hulk'
                  }
              ]
          }
         }

        """
        self.update_capabilities(capability, service)

        if capability:
            if self.updated and self.updated > capability['timestamp']:
                return

            # Update backend level info
            self.update_backend(capability)

            # Update pool level info
            self.update_pools(capability, service)

    def update_pools(self, capability, service):
        """Update storage pools information from backend reported info."""
        if not capability:
            return

        pools = capability.get('pools', None)
        active_pools = set()
        if pools and isinstance(pools, list):
            # Update all pools stats according to information from list
            # of pools in volume capacity
            for pool_cap in pools:
                pool_name = pool_cap['pool_name']
                self._append_backend_info(pool_cap)
                cur_pool = self.pools.get(pool_name, None)
                if not cur_pool:
                    # Add new pool
                    cur_pool = self.pool_state_cls(self.host,
                                                   self.cluster_name,
                                                   pool_cap,
                                                   pool_name)
                    self.pools[pool_name] = cur_pool
                cur_pool.update_from_volume_capability(pool_cap, service)

                active_pools.add(pool_name)
        elif pools is None:
            # To handle legacy driver that doesn't report pool
            # information in the capability, we have to prepare
            # a pool from backend level info, or to update the one
            # we created in self.pools.
            pool_name = self.volume_backend_name
            if pool_name is None:
                # To get DEFAULT_POOL_NAME
                pool_name = vol_utils.extract_host(self.host, 'pool', True)

            if len(self.pools) == 0:
                # No pool was there
                single_pool = self.pool_state_cls(self.host, self.cluster_name,
                                                  capability, pool_name)
                self._append_backend_info(capability)
                self.pools[pool_name] = single_pool
            else:
                # this is an update from legacy driver
                try:
                    single_pool = self.pools[pool_name]
                except KeyError:
                    single_pool = self.pool_state_cls(self.host,
                                                      self.cluster_name,
                                                      capability,
                                                      pool_name)
                    self._append_backend_info(capability)
                    self.pools[pool_name] = single_pool

            single_pool.update_from_volume_capability(capability, service)
            active_pools.add(pool_name)

        # remove non-active pools from self.pools
        nonactive_pools = set(self.pools.keys()) - active_pools
        for pool in nonactive_pools:
            LOG.debug("Removing non-active pool %(pool)s @ %(host)s "
                      "from scheduler cache.", {'pool': pool,
                                                'host': self.host})
            del self.pools[pool]

    def _append_backend_info(self, pool_cap):
        # Fill backend level info to pool if needed.
        if not pool_cap.get('volume_backend_name', None):
            pool_cap['volume_backend_name'] = self.volume_backend_name

        if not pool_cap.get('storage_protocol', None):
            pool_cap['storage_protocol'] = self.storage_protocol

        if not pool_cap.get('vendor_name', None):
            pool_cap['vendor_name'] = self.vendor_name

        if not pool_cap.get('driver_version', None):
            pool_cap['driver_version'] = self.driver_version

        if not pool_cap.get('timestamp', None):
            pool_cap['timestamp'] = self.updated

    def update_backend(self, capability):
        self.volume_backend_name = capability.get('volume_backend_name', None)
        self.vendor_name = capability.get('vendor_name', None)
        self.driver_version = capability.get('driver_version', None)
        self.storage_protocol = capability.get('storage_protocol', None)
        self.updated = capability['timestamp']

    def __repr__(self):
        # FIXME(zhiteng) backend level free_capacity_gb isn't as
        # meaningful as it used to be before pool is introduced, we'd
        # come up with better representation of HostState.
        grouping = 'cluster' if self.cluster_name else 'host'
        grouping_name = self.backend_id
        return ("%(grouping)s '%(grouping_name)s':"
                "free_capacity_gb: %(free_capacity_gb)s, "
                "total_capacity_gb: %(total_capacity_gb)s,"
                "allocated_capacity_gb: %(allocated_capacity_gb)s, "
                "max_over_subscription_ratio: %(mosr)s,"
                "reserved_percentage: %(reserved_percentage)s, "
                "provisioned_capacity_gb: %(provisioned_capacity_gb)s,"
                "thin_provisioning_support: %(thin_provisioning_support)s, "
                "thick_provisioning_support: %(thick)s,"
                "pools: %(pools)s,"
                "updated at: %(updated)s" %
                {'grouping': grouping, 'grouping_name': grouping_name,
                 'free_capacity_gb': self.free_capacity_gb,
                 'total_capacity_gb': self.total_capacity_gb,
                 'allocated_capacity_gb': self.allocated_capacity_gb,
                 'mosr': self.max_over_subscription_ratio,
                 'reserved_percentage': self.reserved_percentage,
                 'provisioned_capacity_gb': self.provisioned_capacity_gb,
                 'thin_provisioning_support': self.thin_provisioning_support,
                 'thick': self.thick_provisioning_support,
                 'pools': self.pools, 'updated': self.updated})


class PoolState(BackendState):
    def __init__(self, host, cluster_name, capabilities, pool_name):
        new_host = vol_utils.append_host(host, pool_name)
        new_cluster = vol_utils.append_host(cluster_name, pool_name)
        super(PoolState, self).__init__(new_host, new_cluster, capabilities)
        self.pool_name = pool_name
        # No pools in pool
        self.pools = None

    def update_from_volume_capability(self, capability, service=None):
        """Update information about a pool from its volume_node info."""
        LOG.debug("Updating capabilities for %s: %s", self.host, capability)
        self.update_capabilities(capability, service)
        if capability:
            if self.updated and self.updated > capability['timestamp']:
                return
            self.update_backend(capability)

            self.total_capacity_gb = capability.get('total_capacity_gb', 0)
            self.free_capacity_gb = capability.get('free_capacity_gb', 0)
            self.allocated_capacity_gb = capability.get(
                'allocated_capacity_gb', 0)
            self.QoS_support = capability.get('QoS_support', False)
            self.reserved_percentage = capability.get('reserved_percentage', 0)
            # provisioned_capacity_gb is the apparent total capacity of
            # all the volumes created on a backend, which is greater than
            # or equal to allocated_capacity_gb, which is the apparent
            # total capacity of all the volumes created on a backend
            # in Cinder. Using allocated_capacity_gb as the default of
            # provisioned_capacity_gb if it is not set.
            self.provisioned_capacity_gb = capability.get(
                'provisioned_capacity_gb', self.allocated_capacity_gb)
            self.thin_provisioning_support = capability.get(
                'thin_provisioning_support', False)
            self.thick_provisioning_support = capability.get(
                'thick_provisioning_support', False)

            self.max_over_subscription_ratio = (
                utils.calculate_max_over_subscription_ratio(
                    capability, CONF.max_over_subscription_ratio))

            self.multiattach = capability.get('multiattach', False)

    def update_pools(self, capability):
        # Do nothing, since we don't have pools within pool, yet
        pass


class HostManager(object):
    """Base HostManager class."""

    backend_state_cls = BackendState

    def __init__(self, manager=None):
        self.store_driver = importutils.import_object(
            CONF.backend_store_driver)
        self.store_driver.initialize_driver(self, manager=manager)
        self.filter_handler = filters.BackendFilterHandler('cinder.scheduler.'
                                                           'filters')
        self.filter_classes = self.filter_handler.get_all_classes()
        self.enabled_filters = self._choose_backend_filters(
            CONF.scheduler_default_filters)
        self.weight_handler = importutils.import_object(
            CONF.scheduler_weight_handler,
            'cinder.scheduler.weights')
        self.weight_classes = self.weight_handler.get_all_classes()

    def _choose_backend_filters(self, filter_cls_names):
        """Return a list of available filter names.

        This function checks input filter names against a predefined set
        of acceptable filters (all loaded filters). If input is None,
        it uses CONF.scheduler_default_filters instead.
        """
        if not isinstance(filter_cls_names, (list, tuple)):
            filter_cls_names = [filter_cls_names]
        good_filters = []
        bad_filters = []
        for filter_name in filter_cls_names:
            found_class = False
            for cls in self.filter_classes:
                if cls.__name__ == filter_name:
                    found_class = True
                    good_filters.append(cls)
                    break
            if not found_class:
                bad_filters.append(filter_name)
        if bad_filters:
            raise exception.SchedulerHostFilterNotFound(
                filter_name=", ".join(bad_filters))
        return good_filters

    def _choose_backend_weighers(self, weight_cls_names):
        """Return a list of available weigher names.

        This function checks input weigher names against a predefined set
        of acceptable weighers (all loaded weighers).  If input is None,
        it uses CONF.scheduler_default_weighers instead.
        """
        if weight_cls_names is None:
            weight_cls_names = CONF.scheduler_default_weighers
        if not isinstance(weight_cls_names, (list, tuple)):
            weight_cls_names = [weight_cls_names]

        good_weighers = []
        bad_weighers = []
        for weigher_name in weight_cls_names:
            found_class = False
            for cls in self.weight_classes:
                if cls.__name__ == weigher_name:
                    good_weighers.append(cls)
                    found_class = True
                    break
            if not found_class:
                bad_weighers.append(weigher_name)
        if bad_weighers:
            raise exception.SchedulerHostWeigherNotFound(
                weigher_name=", ".join(bad_weighers))
        return good_weighers

    def get_filtered_backends(self, backends, filter_properties,
                              filter_class_names=None):
        """Filter backends and return only ones passing all filters."""
        if filter_class_names is not None:
            filter_classes = self._choose_backend_filters(filter_class_names)
        else:
            filter_classes = self.enabled_filters
        return self.filter_handler.get_filtered_objects(filter_classes,
                                                        backends,
                                                        filter_properties)

    def consume_resources(self, backend, request_spec):
        if isinstance(backend, BackendState):
            backend_id = backend.id
        else:
            backend_id = backend
        resource = vol_utils.build_resource_from_request_spec(
            request_spec=request_spec)
        self.store_driver.consume_resources(backend_id, resource)

    def get_weighed_backends(self, backends, weight_properties,
                             weigher_class_names=None):
        """Weigh the backends."""
        weigher_classes = self._choose_backend_weighers(weigher_class_names)

        weighed_backends = self.weight_handler.get_weighed_objects(
            weigher_classes, backends, weight_properties)

        LOG.debug("Weighed %s", weighed_backends)
        return weighed_backends

    def has_all_capabilities(self):
        ctxt = cinder_context.get_admin_context()
        backend_stats = self.store_driver.get_backend_candidates()
        topic = constants.VOLUME_TOPIC
        volume_services = objects.ServiceList.get_all(ctxt,
                                                      {'topic': topic,
                                                       'disabled': False,
                                                       'frozen': False})
        for service in volume_services.objects:
            if not (backend_stats.get(service.cluster_name) and
                    backend_stats.get(service.host)):
                return False
        return True

    def _update_backend_state_map(self, context, backend_stats=None):

        backend_state_map = {}
        backend_stats = backend_stats or []
        # Get resource usage across the available volume nodes:
        topic = constants.VOLUME_TOPIC
        volume_services = objects.ServiceList.get_all(context,
                                                      {'topic': topic,
                                                       'disabled': False,
                                                       'frozen': False})
        active_backends = set()
        active_hosts = set()
        no_capabilities_backends = set()
        for service in volume_services.objects:
            host = service.host
            if not service.is_up:
                LOG.warning("volume service is down. (host: %s)", host)
                continue

            backend_key = service.service_topic_queue
            # We only pay attention to the first up service of a cluster since
            # they all refer to the same capabilities entry in service_states
            if backend_key in active_backends:
                active_hosts.add(host)
                continue

            # Capabilities may come from the cluster or the host if the service
            # has just been converted to a cluster service.
            capabilities = (backend_stats.get(service.cluster_name) or
                            backend_stats.get(service.host))
            if capabilities is None:
                continue

            # Since the service could have been added or remove from a cluster
            backend_state = backend_state_map.get(backend_key, None)
            if not backend_state:
                backend_state = self.backend_state_cls(
                    host,
                    service.cluster_name,
                    capabilities=capabilities,
                    service=dict(service))
                backend_state_map[backend_key] = backend_state

            # update capabilities and attributes in backend_state
            backend_state.update_from_volume_capability(capabilities,
                                                        service=dict(service))
            active_backends.add(backend_key)

        self._no_capabilities_backends = no_capabilities_backends
        return backend_state_map

    def get_backend_candidates(self, context, request_spec=None):
        resources = vol_utils.build_resource_from_request_spec(request_spec)
        return self.store_driver.get_backend_candidates(context, resources)

    def get_backend_states(self, context, request_spec):
        """Returns a dict of all the backends the HostManager knows about.

        Each of the consumable resources in BackendState are
        populated with capabilities scheduler received from RPC.

        For example:
          {'192.168.1.100': BackendState(), ...}
        """

        backends = self.get_backend_candidates(context, request_spec)
        backend_state_map = self._update_backend_state_map(context, backends)

        # build a pool_state map and return that map instead of
        # backend_state_map
        all_pools = {}
        for backend_key, state in backend_state_map.items():
            for key in state.pools:
                pool = state.pools[key]
                # use backend_key.pool_name to make sure key is unique
                pool_key = '.'.join([backend_key, pool.pool_name])
                all_pools[pool_key] = pool

        return all_pools.values()

    def _filter_pools_by_volume_type(self, context, volume_type, pools):
        """Return the pools filtered by volume type specs"""

        # wrap filter properties only with volume_type
        filter_properties = {
            'context': context,
            'volume_type': volume_type,
            'resource_type': volume_type,
            'qos_specs': volume_type.get('qos_specs'),
        }

        filtered = self.get_filtered_backends(pools.values(),
                                              filter_properties)

        # filter the pools by value
        return {k: v for k, v in pools.items() if v in filtered}

    def get_pools(self, context, filters=None):
        """Returns a dict of all pools on all hosts HostManager knows about."""

        backends = self.get_backend_candidates(context)
        backend_state_map = self._update_backend_state_map(
            context, backend_stats=backends)

        all_pools = {}
        name = volume_type = None
        if filters:
            name = filters.pop('name', None)
            volume_type = filters.pop('volume_type', None)

        for backend_key, state in backend_state_map.items():
            for key in state.pools:
                filtered = False
                pool = state.pools[key]
                # use backend_key.pool_name to make sure key is unique
                pool_key = vol_utils.append_host(backend_key, pool.pool_name)
                new_pool = dict(name=pool_key)
                new_pool.update(dict(capabilities=pool.capabilities))

                if name and new_pool.get('name') != name:
                    continue

                if filters:
                    # filter all other items in capabilities
                    for (attr, value) in filters.items():
                        cap = new_pool.get('capabilities').get(attr)
                        if not self._equal_after_convert(cap, value):
                            filtered = True
                            break

                if not filtered:
                    all_pools[pool_key] = pool

        # filter pools by volume type
        if volume_type:
            volume_type = volume_types.get_by_name_or_id(
                context, volume_type)
            all_pools = (
                self._filter_pools_by_volume_type(context,
                                                  volume_type,
                                                  all_pools))

        # encapsulate pools in format:{name: XXX, capabilities: XXX}
        return [dict(name=key, capabilities=value.capabilities)
                for key, value in all_pools.items()]

    def _equal_after_convert(self, capability, value):

        if isinstance(value, type(capability)) or capability is None:
            return value == capability

        if isinstance(capability, bool):
            return capability == strutils.bool_from_string(value)

        # We can not check or convert value parameter's type in
        # anywhere else.
        # If the capability and value are not in the same type,
        # we just convert them into string to compare them.
        return str(value) == str(capability)
