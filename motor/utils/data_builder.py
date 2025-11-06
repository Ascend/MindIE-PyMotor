from motor.utils.logger import get_logger
from motor.resources.instance import Instance
from motor.resources.endpoint import Endpoint, DeviceInfo
from motor.resources.http_msg_spec import Ranktable, ServerInfo, RegisterMsg

logger = get_logger(__name__)


def build_ins_ranktable(ins: Instance) -> Ranktable:
    """
    Build instance level ranktable, it usually have multiple servers.
    """
    ranktable = Ranktable(
        version="1.2",
        status="completed",
        server_count=str(ins.get_node_managers_num()),
        server_list=[
            ServerInfo(
                server_id=str(nm.pod_ip),
                host_ip=nm.host_ip,
                device=[
                    d 
                    for endpoint in ins.get_endpoints(nm.pod_ip).values()
                    for d in endpoint.device_infos 
                ]
            ) for nm in ins.get_node_managers()
        ],
    )

    logger.debug("Instance %s(id:%s)'s ranktable is %s, json is %s",
                 ins.job_name, ins.id, ranktable, ranktable.model_dump())
    return ranktable

    
def build_pod_ranktable(
    pod_ip: str, 
    pod_device_num:int, 
    rank_offset: int = 0, 
    is_supperpod: bool = True,
) -> Ranktable:
    """
    Build pod level ranktable, it only have on server, so server_list size is 1.
    This function is mainly for test case to build ranktable.
    """
    ranktable = Ranktable(
        version="1.2",
        status="completed",
        server_count="1",
        server_list=[
            ServerInfo(
                server_id=pod_ip,
                host_ip=pod_ip,
                device=[
                    DeviceInfo(
                        device_ip=pod_ip,
                        device_id=str(i),
                        rank_id=str(rank_offset + i),
                        super_device_id="0" if is_supperpod else None,
                    )
                    for i in range(pod_device_num)
                ]
            )
        ],
    )

    logger.debug("Pod %s's ranktable is %s, json is %s",
                 pod_ip, ranktable, ranktable.model_dump())
    return ranktable


def build_endpoints(msg: RegisterMsg, id_offset: int = 0) -> dict[int, Endpoint]:
    """
    Build endpoints for a pod, and return the pod_endpoints.
    The algorithm is as follows:
    each endpoint contains tp * pp device, and for different
    parallel config makes different number of endpoints per pod.

    .e.g: DP=4, TP=1, PP=1. make endpoint: 0, 1, 2, 3, 
            each endpoint contains 1 device.
    .e.g: DP=2, TP=4, PP=2. make endpoint: 0, 1, 
            each endpoint contains 8 devices.

    Endpoint's bussiness port is allocate by its node manager.
    """
    devices = msg.ranktable.server_list[0].device
    devices_per_endpoint = msg.parallel_config.tp_size * msg.parallel_config.pp_size
    
    # Calculate the total number of devices needed
    total_devices_needed = len(msg.bussiness_port) * devices_per_endpoint
    total_devices_available = len(devices)
    
    logger.debug("Building endpoints: %d ports, %d devices per endpoint, \
                 total needed: %d, available: %d",
                 len(msg.bussiness_port), devices_per_endpoint, 
                 total_devices_needed, total_devices_available)
    
    # Check if the number of devices is enough
    if total_devices_needed > total_devices_available:
        logger.warning("Not enough devices: need %d, have %d. Will use available \
                       devices and create fewer endpoints.",
                       total_devices_needed, total_devices_available)
        # Calculate the actual number of endpoints that can be created
        max_endpoints = total_devices_available // devices_per_endpoint
        actual_ports = msg.bussiness_port[:max_endpoints]
        logger.info("Will create %d endpoints instead of %d",
                    max_endpoints, len(msg.bussiness_port))
    else:
        actual_ports = msg.bussiness_port

    pod_endpoints: dict[int, Endpoint] = {}
    for i, port in enumerate(actual_ports):
        # Ensure not to exceed the device list range
        start_idx = devices_per_endpoint * i
        end_idx = start_idx + devices_per_endpoint
        
        if end_idx > len(devices):
            logger.warning("Not enough devices for endpoint %d, skipping", i)
            break
            
        pod_endpoints[i] = Endpoint(
            id=id_offset + i, 
            ip=msg.pod_ip, 
            port=port,
            device_infos=devices[start_idx:end_idx]
        )

    logger.debug("Built %d endpoints for pod %s", len(pod_endpoints), msg.pod_ip)
    return pod_endpoints