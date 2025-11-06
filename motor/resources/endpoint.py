import time
from enum import Enum
from pydantic import BaseModel, Field
from motor.utils.logger import get_logger

HEARTBEAT_TIMEOUT = 5 # 5 second

logger = get_logger(__name__)


class Workload(BaseModel):
    """Workload information for load balancing"""
    active_kv_cache: float = Field(default=0, description="Active KV cache size")
    active_tokens: float = Field(default=0, description="Number of active requests")
    
    def __iadd__(self, other):
        if not isinstance(other, Workload):
            raise TypeError("Unsupported operand type(s) for +=: 'Workload' and %s",
                            type(other).__name__)
        
        self.active_kv_cache += other.active_kv_cache
        self.active_tokens += other.active_tokens
        
        return self
    
    def calculate_workload_score(self, role: None) -> float:
        """
        Calculate workload score based on role.
        
        Args:
            role: PDRole enum indicating the role (prefill/decode/both)
            
        Returns:
            float: Calculated workload score
        """

        role_value = role.value if isinstance(role, Enum) else role
        if role_value == "prefill":
            return self.active_tokens + self.active_kv_cache * 0.3
        elif role_value == "decode":
            return self.active_tokens
        elif role_value == "both":
            return self.active_tokens + self.active_kv_cache * 0.15
        else:
            raise ValueError("Invalid role value: %s", role_value)


class WorkloadAction(Enum):
    ALLOCATION = 'Allocation'
    RELEASE_KV = 'Release_KV'
    RELEASE_TOKENS ='Release_Tokens'


class DeviceInfo(BaseModel):
    device_id: str = Field(..., description="Local rank id")
    device_ip: str = Field(..., description="Device IP address")
    super_device_id: str|None = Field(None, description="Super device id, default is None")
    rank_id: str = Field(..., description="Global rank id")


class EndpointStatus(str, Enum):
    INITIAL = "initial"
    NORMAL = "normal"
    ABNORMAL = "abnormal"
    PAUSED = "paused"
    WAIT2START = "wait2start"

    def __repr__(self) -> str:
        return str.__repr__(self.value)

class Endpoint(BaseModel):
    id: int = Field(..., description="Endpoint ID") 
    ip: str = Field(..., description="IP address") 
    port: str = Field(..., description="Port") 
    status: EndpointStatus = Field(default=EndpointStatus.INITIAL, description="Endpoint status")
    device_infos: list[DeviceInfo] = Field(default_factory=list, description="List of DeviceInfo") 
    hb_timestamp: float = Field(default=0, description="Last heartbeat timestamp")
    workload: Workload = Field(default_factory=Workload, description="Current workload of the endpoint")

    def __init__(
        self,
        id: int,
        ip: str,
        port: str,
        status: EndpointStatus|None = None,
        device_infos: list[DeviceInfo]|None = None,
        hb_timestamp: float|None = None,
        workload: Workload|None = None
    ) -> None:
        super().__init__(
            id=id,
            ip=ip,
            port=port,
            status=status if status is not None else EndpointStatus.INITIAL,
            device_infos=device_infos if device_infos is not None else [],
            hb_timestamp=hb_timestamp if hb_timestamp is not None else time.time(),
            workload=workload if workload is not None else Workload()
        )
        logger.debug("Init endpoint with id:%s ip:%s port:%s", id, ip, port)

    def add_device(self, device_info: DeviceInfo) -> None:
        if device_info not in self.device_infos:
            self.device_infos.append(device_info)

    def del_device(self, device_info: DeviceInfo) -> None:
        if device_info in self.device_infos:
            self.device_infos.remove(device_info)

    def is_alive(self, timestamp: float, heartbeat_timeout: int = HEARTBEAT_TIMEOUT) -> bool:
        return (timestamp - self.hb_timestamp) <= heartbeat_timeout
    