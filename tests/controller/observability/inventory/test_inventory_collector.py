# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
# MindIE is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#         http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

import random
from unittest.mock import patch

import pytest

from motor.common.resources import InsStatus, Instance, DeviceInfo, EndpointStatus, Endpoint, Workload, NodeManagerInfo, \
    ParallelConfig
from motor.common.resources.instance import PDRole
from motor.controller.core import InstanceManager
from motor.controller.observability.inventory.inventory_collector import InventoryCollector, ModelState


@pytest.fixture
def inventory_collector():
    collector = InventoryCollector()
    object.__setattr__(collector, 'active_om_list', [])
    object.__setattr__(collector, 'inactive_om_list', [])
    return collector


@patch("motor.controller.core.InstanceManager.get_active_instances")
@patch("motor.controller.core.InstanceManager.get_initial_instances")
@patch("motor.controller.observability.inventory.inventory_collector.os.getenv")
@patch("motor.controller.observability.inventory.inventory_collector.time.time")
def test_collect_inventory_normal_case(
        mock_time, mock_getenv, mock_get_inactive, mock_get_active, inventory_collector
):
    """
    Test normal situation
    """

    InstanceManager().instances = {}
    # mock input
    mock_get_active.return_value = mock_input()
    mock_get_inactive.return_value = []
    mock_getenv.return_value = "model_123"
    mock_time.return_value = 1698765432.123

    # call test_function
    result = inventory_collector.collect_inventory()

    # assert check
    assert result.get("modelName") == "qwen3-8B"
    assert result.get("modelType") == "qwen3-8B"
    assert result.get("modelID") == "model_123"
    assert result.get("modelState") == ModelState.HEALTHY

    inventory = result.get("inventories")
    p_instance_list = inventory.get("PInstanceList")
    d_instance_list = inventory.get("DInstanceList")
    assert len(d_instance_list) == 1
    assert len(p_instance_list) == 2
    for instance in p_instance_list:
        assert len(instance.get("serverIPList")) == 0
        assert len(instance.get("podInfoList")) == 2
        for pod_info in instance.get("podInfoList"):
            assert len(pod_info.get("podAssociatedInfoList")) == 8

    for instance in d_instance_list:
        assert len(instance.get("podInfoList")) == 4
        for pod_info in instance.get("podInfoList"):
            assert len(pod_info.get("podAssociatedInfoList")) == 8

    dp_group_list = inventory.get("DPGroupList")
    # 2*2P+1*4D, p have 4 pd_group, 8 card = 1 pd_group; d have 16 pd_group, 2 card = 1 pd_group
    assert len(dp_group_list) == 2 * 2 + 1 * 4 * 4
    assert len(inventory.get("serverIPList")) == 0


@patch("motor.controller.core.InstanceManager.get_active_instances")
@patch("motor.controller.core.InstanceManager.get_initial_instances")
def test_collect_inventory_subhealth_case(
        mock_get_inactive_job_count, mock_get_active, inventory_collector
):
    """
    Test sub_health situation
    """
    InstanceManager().instances = {}

    mock_get_active.return_value = mock_input(2, 1)
    mock_get_inactive_job_count.return_value = mock_input(1, 0)

    result = inventory_collector.collect_inventory()
    inventory = result.get("inventories")
    p_instance_list = inventory.get("PInstanceList")
    assert result.get("modelState") == ModelState.SUB_HEALTHY
    assert len(p_instance_list) == 2 + 1


@patch("motor.controller.core.InstanceManager.get_active_instances")
@patch("motor.controller.core.InstanceManager.get_inactive_instances")
def test_collect_inventory_unhealth_case(
        mock_get_active, mock_get_inactive, inventory_collector
):
    """
    Test un_health situation
    """

    mock_get_active.return_value = mock_input(p_num=1, d_num=1)
    mock_get_inactive.return_value = mock_input(p_num=0, d_num=1)

    result = inventory_collector.collect_inventory()
    assert result.get("modelState") == ModelState.UNHEALTHY


def mock_device_list(device_num: int):
    device_num_list = []
    for i in range(device_num):
        device_num_list.append(
            DeviceInfo(device_id=str(i), device_ip='10.0.245.1' + str(i), super_device_id='167772000', rank_id=str(i)))
    return device_num_list


def mock_input(p_num: int | None = 2, d_num: int | None = 1):
    parallel_config = ParallelConfig(dp_size=16, cp_size=1, tp_size=2, sp_size=1, ep_size=1, pp_size=1, world_size=32)
    parallel_config_p = ParallelConfig(dp_size=2, cp_size=1, tp_size=16, sp_size=1, ep_size=1, pp_size=1, world_size=16)
    test_node_manager_info_0 = NodeManagerInfo(pod_ip='192.168.222.213', port='1026')
    test_node_manager_info_1 = NodeManagerInfo(pod_ip='192.168.222.214', port='1026')
    test_node_manager_info_2 = NodeManagerInfo(pod_ip='192.168.222.215', port='1026')
    test_node_manager_info_3 = NodeManagerInfo(pod_ip='192.168.222.216', port='1026')

    test_node_manager_info_p_0 = NodeManagerInfo(pod_ip='192.168.222.211', port='1026')
    test_node_manager_info_p_1 = NodeManagerInfo(pod_ip='192.168.222.212', port='1026')

    test_endpoint_0 = Endpoint(
        id=0, ip='192.168.222.208', business_port='10000', mgmt_port='10001', status=EndpointStatus.NORMAL,
        device_infos=mock_device_list(2), hb_timestamp=1770171687,
        workload=Workload(active_kv_cache=0, active_tokens=0))

    test_endpoint_1 = Endpoint(
        id=1, ip='192.168.222.210', business_port='10001', mgmt_port='10002',
        status=EndpointStatus.NORMAL, device_infos=mock_device_list(2),
        hb_timestamp=1770171687.2293227, workload=Workload(active_kv_cache=0, active_tokens=0))

    test_endpoint_16_1 = Endpoint(
        id=0, ip='192.168.222.211', business_port='10002', mgmt_port='10002',
        status=EndpointStatus.NORMAL, device_infos=mock_device_list(8),
        hb_timestamp=1770171687.22930, workload=Workload(active_kv_cache=0, active_tokens=0))

    test_endpoint_16_2 = Endpoint(
        id=1, ip='192.168.222.212', business_port='10002', mgmt_port='10002',
        status=EndpointStatus.NORMAL, device_infos=mock_device_list(8),
        hb_timestamp=1770171687.2293230, workload=Workload(active_kv_cache=0, active_tokens=0))

    # mock instance
    temp_input = []

    for i in range(p_num):
        test_instance_p = Instance(
            job_name="mindie-pymotor-p" + str(i) + "-" + str(random.randint(100000, 999999)), model_name='qwen3-8B',
            id=1, role=PDRole.ROLE_P, status=InsStatus.ACTIVE, parallel_config=parallel_config_p,
            node_managers=[test_node_manager_info_p_0, test_node_manager_info_p_1],
            endpoints={"192.168.222.211": {0: test_endpoint_16_1}, "192.168.222.212": {1: test_endpoint_16_2}})
        temp_input.append(test_instance_p)
    for i in range(d_num):
        test_instance_d = Instance(
            job_name="mindie-pymotor-d" + str(i) + "-" + str(random.randint(100000, 999999)), model_name='qwen3-8B',
            id=1, role=PDRole.ROLE_D, status=InsStatus.ACTIVE, parallel_config=parallel_config,
            node_managers=[test_node_manager_info_0, test_node_manager_info_1,
                           test_node_manager_info_2, test_node_manager_info_3],
            endpoints={
                "192.168.222.213": {0: test_endpoint_0, 1: test_endpoint_1, 2: test_endpoint_1, 3: test_endpoint_1},
                "192.168.222.214": {4: test_endpoint_0, 5: test_endpoint_1, 6: test_endpoint_1, 7: test_endpoint_1},
                "192.168.222.215": {8: test_endpoint_0, 9: test_endpoint_1, 10: test_endpoint_1, 11: test_endpoint_1},
                "192.168.222.216": {12: test_endpoint_0, 13: test_endpoint_1, 14: test_endpoint_1, 15: test_endpoint_1}}
        )
        temp_input.append(test_instance_d)
    return temp_input
