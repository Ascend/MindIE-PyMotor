import time
import threading
from enum import Enum
from pydantic import BaseModel, Field
from motor.utils.logger import get_logger
from motor.resources.endpoint import Endpoint, EndpointStatus, Workload

logger = get_logger(__name__)

ACTIVE_INSTANCE_HEARTBEAT_TIMEOUT = 5
CLEAR_INSTANCE_TIMEOUT = 300


class InsStatus(str, Enum):
    INITIAL = "initial"
    INACTIVE = "inactive"
    ACTIVE = "active"
    DELTETED = "deleted"

    def __repr__(self) -> str:
        return str.__repr__(self.value)  # 序列化时返回值的表示

class PDRole(str, Enum):
    ROLE_P = "prefill"
    ROLE_D = "decode"
    ROLE_U = "both"
    def __repr__(self) -> str:
        return str.__repr__(self.value)

# Instance condition event
class InsConditionEvent(str, Enum):
    INSTANCE_INIT = "instance_init"
    INSTANCE_HEARTBEAT_TIMEOUT = "instance_heartbeat_timeout"
    INSTANCE_NORMAL = "instance_normal"
    INSTANCE_ABNORMAL = "instance_abnormal"

    def __repr__(self) -> str:
        return str.__repr__(self.value)


class NodeManagerInfo(BaseModel):
    pod_ip: str = Field(..., description="Node manager pod ip")
    host_ip: str = Field(..., description="Node manager host ip")
    port: str = Field(..., description="Node manager port")


class ParallelConfig(BaseModel):
    dp_size: int = Field(default=1, description="Data parallel size")
    cp_size: int = Field(default=1, description="Context parallel size")
    tp_size: int = Field(default=1, description="Tensor parallel size")
    sp_size: int = Field(default=1, description="Sequence parallel size, usually it reuse tp's pg")
    ep_size: int = Field(default=1, description="Expert parallel size")
    pp_size: int = Field(default=1, description="Pipeline parallel size")
    world_size: int = Field(default=0)

    def __init__(
            self,
            dp: int = None,
            cp: int = None,
            tp: int = None,
            sp: int = None,
            ep: int = None,
            pp: int = None,
            world_size: int = None,
            # support field name parameters, for JSON deserialization
            dp_size: int = None,
            cp_size: int = None,
            tp_size: int = None,
            sp_size: int = None,
            ep_size: int = None,
            pp_size: int = None,
            **kwargs
    ) -> None:
        dp_val = dp_size if dp_size is not None else (dp if dp is not None else 1)
        cp_val = cp_size if cp_size is not None else (cp if cp is not None else 1)
        tp_val = tp_size if tp_size is not None else (tp if tp is not None else 1)
        sp_val = sp_size if sp_size is not None else (sp if sp is not None else 1)
        ep_val = ep_size if ep_size is not None else (ep if ep is not None else 1)
        pp_val = pp_size if pp_size is not None else (pp if pp is not None else 1)
        world_size_val = world_size if world_size is not None else 0
        
        if world_size_val == 0:
            world_size_val = dp_val * cp_val * tp_val * pp_val
            
        super().__init__(
            dp_size=dp_val,
            cp_size=cp_val,
            tp_size=tp_val,
            sp_size=sp_val,
            ep_size=ep_val,
            pp_size=pp_val,
            world_size=world_size_val,
        )
        logger.debug(f"ParallelConfig initialized with dp:{dp_val}, cp:{cp_val}, "
                    f"tp:{tp_val}, sp:{sp_val}, ep:{ep_val}, pp:{pp_val}, world_size:{world_size_val}")


class Instance(BaseModel):
    """
    instance is a group of endpoints, it can be prefill or decode
    """
    job_name: str = Field(..., description="Instance job name")
    model_name: str = Field(..., description="Instance model name")
    id: int = Field(..., description="Instance ID")
    role: str = Field(..., description="Instance role")
    status: InsStatus = Field(default=InsStatus.INITIAL, description="Instance status")
    group_id: int = Field(default=0, description="Instance group id")
    parallel_config: ParallelConfig | None = Field(None, description="Parallel configuration")
    node_managers: list[NodeManagerInfo] = Field(default_factory=list,
                                             description="List of node manager info")
    endpoints: dict[str, dict[int, Endpoint]] = Field(default_factory=dict,
                                                      description="Mapping of endpoints by pod IP")
    gathered_workload: Workload = Field(default_factory=Workload,
                                        description="Gathered workload of all endpoints in the instance")

    def __init__(self, **data) -> None:
        super().__init__(**data)
        self._lock = threading.Lock()

    def add_node_mgr(self, pod_ip: str, host_ip: str, port: str) -> None:
        if pod_ip is None or host_ip is None or port is None:
            logger.info(f"Invalid ip:{pod_ip} or host_ip:{host_ip} or port:{port}")
            return

        node_mgr_info = NodeManagerInfo(pod_ip=pod_ip, host_ip=host_ip, port=port)
        with self._lock:
            if node_mgr_info not in self.node_managers:
                self.node_managers.append(node_mgr_info)
                logger.info(f"Add node manager {pod_ip}:{port} to instance:{self.job_name}")
            else:
                logger.info(f"Node manager {pod_ip}:{port} already in instance:{self.job_name}")

    def del_node_mgr(self, pod_ip: str, host_ip: str, port: str) -> None:
        if pod_ip is None or host_ip is None or port is None:
            logger.info(f"Invalid ip:{pod_ip} or host_ip:{host_ip} or port:{port}")
            return

        node_mgr_info = NodeManagerInfo(pod_ip=pod_ip, host_ip=host_ip, port=port)
        with self._lock:
            if node_mgr_info in self.node_managers:
                self.node_mgrs.remove(node_mgr_info)
                logger.info(f"Del node manager {pod_ip}:{port} from instance:{self.job_name}")
            else:
                logger.info(f"Node manager {pod_ip}:{port} not in instance:{self.job_name}")

    def add_endpoints(self, pod_ip: str, endpoints: dict[int, Endpoint]) -> None:
        if endpoints is None or not isinstance(endpoints, dict):
            logger.info(f"Invalid endpoints for pod_ip:{pod_ip}")
            return

        add_endpoint_num = len(endpoints.values())
        current_endpoint_num = self.get_endpoints_num()

        with self._lock:
            self.endpoints[pod_ip] = endpoints
        logger.info(f"Add endpoints for pod_ip:{pod_ip}, added endpoints "
                    f"number is {add_endpoint_num}, total endpoints "
                    f"number is {current_endpoint_num + add_endpoint_num}")

    def del_endpoints(self, pod_ip: str):
        current_endpoint_num = self.get_endpoints_num()
        with self._lock:
            if pod_ip in self.endpoints:
                del_endpoint_num = len(self.endpoints[pod_ip])
                del self.endpoints[pod_ip]
            else:
                logger.warning(f"Pod_ip:{pod_ip} not found in instance:{self.job_name}")

        logger.info(f"Del endpoints for pod_ip:{pod_ip}, deleted endpoints "
                    f"number is {del_endpoint_num}, total endpoints number "
                    f"is {current_endpoint_num - del_endpoint_num}")

    def is_endpoints_enough(self) -> bool:
        """
            if endpoints number is equals to dp size, 
            then we think this instance is ready.

        Returns:
            bool: wheter this instance is ready
        """
        with self._lock:
            if self.parallel_config is not None:
                dp_size = self.parallel_config.dp_size
        if self.endpoints is not None:
            total_endpoints = self.get_endpoints_num()
            logger.debug(f"total endpoint size: {total_endpoints} dp size: {dp_size}")
            if total_endpoints == dp_size:
                logger.info(f"Instance {self.id} is assembled now, "
                            f"endpoints number is {total_endpoints}")
                return True
        return False

    def is_all_endpoints_alive(self) -> bool:
        timestamp = time.time()

        if self.status == InsStatus.ACTIVE:
            timeout = ACTIVE_INSTANCE_HEARTBEAT_TIMEOUT
        else:
            timeout = CLEAR_INSTANCE_TIMEOUT

        with self._lock:
            for pod_endpoints in self.endpoints.values():
                for endpoint in pod_endpoints.values():
                    if not endpoint.is_alive(timestamp, timeout):
                        return False
            return True

    # Check if all endpoints of the instance are normal
    def is_all_endpoints_ready(self) -> bool:
        with self._lock:
            for pod_endpoints in self.endpoints.values():
                for endpoint in pod_endpoints.values():
                    if endpoint.status != EndpointStatus.NORMAL:
                        return False
            return True

    # Check if there is an endpoint with abnormal status
    def is_have_one_endpoint_abnormal(self) -> bool:
        with self._lock:
            for pod_endpoints in self.endpoints.values():
                for endpoint in pod_endpoints.values():
                    if endpoint.status == EndpointStatus.ABNORMAL:
                        return True
            return False

    def is_ip_in_endpoints(self, ip: str) -> bool:
        with self._lock:
            return ip in self.endpoints

    def update_heartbeat(self, ip: str, timestamp: float, status: dict[int, EndpointStatus]) -> bool:
        with self._lock:
            if ip in self.endpoints:
                if len(self.endpoints[ip]) != len(status):
                    logger.error(f"Heartbeat status size {len(status)} is not equal to "
                                 f"endpoints size {len(self.endpoints[ip])} for pod_ip {ip} "
                                 f"in instance {self.job_name}")
                    return False
                for (i, endpoint) in enumerate(self.endpoints[ip].values()):
                    endpoint.hb_timestamp = timestamp
                    endpoint.status = status[i]
                logger.debug(f"Updated heartbeat for pod_ip {ip} in instance {self.job_name}")
                return True
            else:
                logger.error(f"Instance {self.id} not found endpoints for pod_ip {ip}")
                return False

    def set_group_id(self, group_id: int) -> None:
        with self._lock:
            self.group_id = group_id

    def get_group_id(self) -> None:
        with self._lock:
            return self.group_id

    def get_endpoints_num(self) -> int:
        with self._lock:
            if self.endpoints is not None:
                return sum([len(pod_endpoints) for pod_endpoints in self.endpoints.values()])
            return 0

    def get_endpoints(self, ip: str) -> dict[int, Endpoint]:
        """ Get endpoints by server ip """
        with self._lock:
            return self.endpoints[ip] if ip in self.endpoints else None

    def get_all_endpoints(self) -> list[Endpoint]:
        with self._lock:
            eps = []
            for pod_endpoints in self.endpoints.values():
                for endpoint in pod_endpoints.values():
                    eps.append(endpoint)
            return eps

    def get_node_managers_num(self) -> int:
        with self._lock:
            return len(self.node_managers)

    def get_node_managers(self) -> list[NodeManagerInfo]:
        with self._lock:
            return self.node_managers.copy()

    def update_instance_status(self, status: InsStatus) -> None:
        with self._lock:
            self.status = status
        logger.info(f"Instance {self.job_name}(id:{self.id}) status updated to {status}")
