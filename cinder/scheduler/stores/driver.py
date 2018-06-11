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


class StoreDriver(object):
    """The base class that all Store Driver classes should inherit from."""

    def initialize_driver(self, context, manager):
        """Initialize driver within manager instance

        This method will be performed when initialize driver at the
        scheduler node.

        :param context:      The context of the caller.
        :param manager:      The manager instance.
        """

    def get_backend_candidates(self, context, resources):
        """Get valid backend candidates

        This method is typically used in scheduler when creating new
        resources the parameter 'resources' which represents the
        requirements will be:

        .. code-block:: javascript

        {
            "VOLUME_GB": 1024
        }
        NOTE: driver need to build the BackendState object rather than
        status dictionary when returning.

        :param context:          The context of the caller.
        :param resources:        The resource requirement.
        :return: Lists of BackendState who can hold the specified resource.

        """
        return []

    def update_backend_capabilities(self, context,
                                    backend_identity, capabilities):
        """ Update backend pools' status

        This method is typically used by volume service's periodic task,
        the 'capabilities' is a raw data collected from drivers in the
        format of:

        .. code-block:: javascript

        {
            "filter_function": "",
            "goodness_function": "",
            "shared_targets": false,
            "volume_backend_name": "lvmdriver-1",
            "driver_version": "3.0.0",
            "sparse_copy_volume": true,
            "pools": [
                {
                    "pool_name": "lvmdriver-1",
                    "filter_function": "",
                    "goodness_function": "",
                    "multiattach": true,
                    "total_volumes": "1 1",
                    "provisioned_capacity_gb": 10.0,
                    "allocated_capacity_gb": 10,
                    "thin_provisioning_support": true,
                    "free_capacity_gb": 9.32,
                    "location_info": "LVMVolumeDriver:h:lvmgroup:thin:0",
                    "total_capacity_gb": 9.51,
                    "thick_provisioning_support": false,
                    "reserved_percentage": 0,
                    "QoS_support": false,
                    "max_over_subscription_ratio": "20.0",
                    "backend_state": "up"
                }
            ],
            "vendor_name": "Open Source",
            "storage_protocol": "iSCSI"
        }

        driver needs to update the corresponding backend data as well as
        notify metering system.

        :param context:          The context of the caller.
        :param service_name:     The name of the service.
        :param backend_identity: Backend-specific information used to
                                 identity a backend data.
        :param capabilities:     The raw capabilities information
                                 collected from driver.
        """
        return

    def consume_resources(self, backend_identity, resources):
        """Update backend status within resources

         This is used when resource successfully schedule in the scheduler
         or created in the backend. The resource number can be negative
         which means revert consuming operation. 'resources' will in the
         format of:

         .. code-block:: javascript

         {
            "VOLUME_GB": 1024
         }

         When consuming one resource in one specific pool. Three main
         attributes of pool will be update.

         1. free_capacity_gb
         2. allocated_capacity_gb
         3. provisioned_capacity_gb

         :param backend_identity: Backend-specific information used to
                                  identity a backend data.
         :param resources:        The resource requirement.

         """
        return

