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

from datetime import datetime
import types

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import timeutils

from cinder import exception
from cinder import rpc
from cinder.scheduler import rpcapi
from cinder.scheduler.stores import driver
from cinder.scheduler.stores import store_utils

LOG = logging.getLogger(__name__)


CONF = cfg.CONF


class MemoryRpcClient(rpcapi.SchedulerAPI):

    def update_service_capabilities(self, ctxt, service_name, host,
                                    capabilities, cluster_name,
                                    timestamp=None):
        msg_args = dict(service_name=service_name, host=host,
                        capabilities=capabilities)

        version = '3.3'
        # If server accepts timestamping the capabilities and the cluster name
        if self.client.can_send_version(version):
            # Serialize the timestamp
            msg_args.update(cluster_name=cluster_name,
                            timestamp=self.prepare_timestamp(timestamp))
        else:
            version = '3.0'

        cctxt = self._get_cctxt(fanout=True, version=version)
        cctxt.cast(ctxt, 'update_service_capabilities', **msg_args)

    @rpc.assert_min_rpc_version('3.1')
    def notify_service_capabilities(self, ctxt, service_name,
                                    backend, capabilities, timestamp=None):
        parameters = {'service_name': service_name,
                      'capabilities': capabilities}
        if self.client.can_send_version('3.5'):
            version = '3.5'
            parameters.update(backend=backend,
                              timestamp=self.prepare_timestamp(timestamp))
        else:
            version = '3.1'
            parameters['host'] = backend

        cctxt = self._get_cctxt(version=version)
        cctxt.cast(ctxt, 'notify_service_capabilities', **parameters)


class MemoryStoreDriver(driver.StoreDriver):
    """Memory Store Driver use RPC to deliver backend states to scheduler
    hosts and keep it there updated in memory."""

    def __init__(self):
        self.rpcclient = MemoryRpcClient()
        self.service_states = {}  # { <host|cluster>: {<service>: {cap k : v}}}
        self.service_notify_pools = {}

    def _notify_service_capabilities(self, context,
                                     service_name,
                                     capabilities, host=None,
                                     backend=None, timestamp=None):
        """Process a capability update from a service node."""
        # TODO(geguileo): On v4 remove host field.
        if capabilities is None:
            capabilities = {}
        # If we received the timestamp we have to deserialize it
        elif timestamp:
            timestamp = datetime.strptime(timestamp,
                                          timeutils.PERFECT_TIME_FORMAT)
        backend = backend or host
        """Notify the ceilometer with updated volume stats"""
        if service_name != 'volume':
            return
        notify_pools = self.service_notify_pools[backend]

        if notify_pools:
            store_utils.get_usage_and_notify(capabilities, notify_pools,
                                             backend, timestamp)
            self.service_notify_pools.pop(backend, None)

    def _update_service_capabilities(self, context, service_name, host,
                                     capabilities,
                                     cluster_name, timestamp):
        """Update the per-service capabilities based on this notification."""

        if capabilities is None:
            capabilities = {}
        # If we received the timestamp we have to deserialize it
        elif timestamp:
            timestamp = datetime.strptime(timestamp,
                                          timeutils.PERFECT_TIME_FORMAT)

        # TODO(geguileo): In P - Remove the next line since we receive the
        # timestamp
        timestamp = timestamp or timeutils.utcnow()
        # Copy the capabilities, so we don't modify the original dict
        capab_copy = dict(capabilities)
        capab_copy["timestamp"] = timestamp

        # Set the default capabilities in case None is set.
        backend = cluster_name or host
        capab_old = self.service_states.get(backend, {"timestamp": 0})

        # Ignore older updates
        if capab_old['timestamp'] and timestamp < capab_old['timestamp']:
            LOG.info('Ignoring old capability report from %s.', backend)
            return

        # If the capabilities are not changed and the timestamp is older,
        # record the capabilities.

        # There are cases: capab_old has the capabilities set,
        # but the timestamp may be None in it. So does capab_last_update.

        notify_pools = store_utils.get_updated_pools(capab_old, capab_copy)
        if notify_pools:
            self.service_notify_pools[backend] = notify_pools
        self.service_states[backend] = capab_copy

        cluster_msg = (('Cluster: %s - Host: ' % cluster_name) if cluster_name
                       else '')
        LOG.debug("Received %(service_name)s service update from %(cluster)s"
                  "%(host)s: %(cap)s%(cluster)s",
                  {'service_name': service_name, 'host': host,
                   'cap': capabilities,
                   'cluster': cluster_msg})

    def initialize_driver(self, context, manager):

        store_driver = self

        def update_service_capabilities(self, context, service_name=None,
                                        host=None, capabilities=None,
                                        cluster_name=None, timestamp=None):
            store_driver._update_service_capabilities(
                context=context,
                service_name=service_name, host=host,
                capabilities=capabilities,
                cluster_name=cluster_name,
                timestamp=timestamp)

        def notify_service_capabilities(self, context, service_name,
                                        capabilities, host=None,
                                        backend=None, timestamp=None):
            store_driver._notify_service_capabilities(
                context, service_name, capabilities, host=host,
                backend=backend, timestamp=timestamp)

        # We will bound our rpc endpoints to scheduler manager in order to
        # handle request from volume service.
        manager.update_service_capabilities = types.MethodType(
            update_service_capabilities, manager)

        manager.notify_service_capabilities = types.MethodType(
            notify_service_capabilities, manager)

    def update_backend_capabilities(self, context,
                                    backend_identity, capabilities):
        try:
            self.rpcclient.update_service_capabilities(
                context, 'volume', backend_identity,
                timestamp=None)
            self.rpcclient.notify_service_capabilities(
                context,
                'volume',
                backend_identity,
                capabilities,
                timestamp=None)
        except exception.ServiceTooOld as e:
            # This means we have Newton's c-sch in the deployment, so
            # rpcapi cannot send the message. We can safely ignore the
            # error. Log it because it shouldn't happen after upgrade.
            msg = ("Failed to notify about cinder-volume service "
                   "capabilities for host %(host)s. This is normal "
                   "during a live upgrade. Error: %(e)s")
            LOG.warning(msg, {'host': backend_identity, 'e': e})

    def get_backend_candidates(self, context, resource):
        # NOTE(tommylikehu): In order to keep consistency, we will not filter
        # backend based on request resource, just return all of the backends.
        return self.service_states

    def consume_resources(self, backend_identity, resource):
        size = resource.get('VOLUME_GB', 0)
        if size:
            backend = self.service_states.get(backend_identity, {})
            if backend:
                backend['allocated_capacity_gb'] += size
                backend['provisioned_capacity_gb'] += size
                if backend['free_capacity_gb'] == 'infinite':
                    # There's virtually infinite space on back-end
                    pass
                elif backend['free_capacity_gb'] == 'unknown':
                    # Unable to determine the actual free space on back-end
                    pass
                else:
                    backend['free_capacity_gb'] -= size
