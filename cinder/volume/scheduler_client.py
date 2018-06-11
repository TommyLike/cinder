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
from oslo_utils import importutils

LOG = logging.getLogger(__name__)

CONF = cfg.CONF
CONF.import_opt('scheduler_store_driver')


class SchedulerClient(object):

    def __init__(self):
        self.store_driver = importutils.import_object(
            CONF.scheduler_store_driver)

    def update_backend_capabilities(self,  context,
                                    service_name,
                                    host,
                                    capabilities,
                                    cluster):
        if service_name != 'volume':
            LOG.debug('Ignoring %(service_name)s service update '
                      'from %(host)s',
                      {'service_name': service_name, 'host': host})
            return
        self.store_driver.update_backend_capabilities(
            self, context, service_name, cluster if cluster else host,
            capabilities)
