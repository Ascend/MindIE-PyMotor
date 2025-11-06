from pydantic import BaseModel, Field
from enum import Enum

from motor.utils.logger import get_logger
from motor.resources.instance import Instance, ParallelConfig
from motor.resources.endpoint import Endpoint, DeviceInfo, EndpointStatus

logger = get_logger(__name__)


class ServerInfo(BaseModel):
    server_id: str = Field(..., description="Server ID")
    host_ip: str = Field(..., description="Host IP address")
    device: list[DeviceInfo] = Field(..., description="List of DeviceInfo")


class Ranktable(BaseModel):
    """
    Instance level ranktable, it is unified between different infer engine
    """
    version: str = Field(..., description="")
    status: str = Field(..., description="")
    server_count: str = Field(..., description="")
    server_list: list[ServerInfo] = Field(..., description="List of ServerInfo")


class RegisterMsg(BaseModel):
    """
    Registration message format sent from NodeManager to controller.
    """
    job_name: str = Field(..., description="Instance job name")
    model_name: str = Field(..., description="Instance model name")
    role: str = Field(..., description="Instance role")
    pod_ip: str = Field(..., description="Pod IP address")
    host_ip: str = Field(..., description="Host IP address")
    bussiness_port: list[str] = Field(..., description="Business port for all endpoints managed by this nm")
    mgmt_port: str = Field(..., description="Node manager api port, mostly used for management and health check")
    parallel_config: ParallelConfig = Field(..., description="Parallel configuration")
    ranktable: Ranktable = Field(..., description="Ranktable managed by this nm")


class StartCmdMsg(BaseModel):
    """
    Start command message format sent from controller to NodeManager.
    This msg brings the necessary information .e.g instance's ranktable 
    and instance id and role for NodeManager to start the instance.
    """
    job_name: str = Field(..., description="Instance job name")
    role: str = Field(..., description="Instance role")
    instance_id: int = Field(..., description="Instance id")
    endpoints: list[Endpoint] = Field(..., description="endpoints that managed by nm")
    ranktable: Ranktable = Field(..., description="DeviceInfo list of this instance")


class ReregisterMsg(BaseModel):
    """
    Re-register message format sent from NodeManager to controller.
    It only occured when controller restarts and NodeManager needs to
    re-register to controller.
    """
    job_name: str = Field(..., description="Instance job name")
    model_name: str = Field(..., description="Instance model name")
    instance_id: int = Field(..., description="Instance id")
    role: str = Field(..., description="Instance role")
    pod_ip: str = Field(..., description="Pod IP address")
    host_ip: str = Field(..., description="Host IP address")
    mgmt_port: str = Field(..., description="Management port")
    parallel_config: ParallelConfig = Field(..., description="Parallel configuration")
    endpoints: list[Endpoint] = Field(..., description="endpoints that managed by nm")


class HeartbeatMsg(BaseModel):
    """
    Heartbeat message format sent from NodeManager to controller.
    """
    job_name: str = Field(..., description="Instance job name")
    ins_id: int = Field(..., description="Instance id")
    ip: str = Field(..., description="Pod IP address")
    status: dict[int, EndpointStatus] = Field(..., description="Endpoints status list")


class TerminateInstanceMsg(BaseModel):
    """
    Heartbeat message format sent from NodeManager to controller.
    """
    instance_id: str = Field(..., description="Instance id")
    reason: str = Field(..., description="The reason for terminating the instance")


class EventType(str, Enum):
    """
    Event types for instance events, currently include add, delete, and set.
    And used by EventPusher to notify the coordinator.
    """
    ADD = "add"
    DEL = "del"
    SET = "set"

    def __repr__(self) -> str:
        return str.__repr__(self.value)  # 序列化时返回值的表示


class InsEventMsg(BaseModel):
    """
    Message format for instance events to be sent to the coordinator.
    Add and delete events carry a list of instances, while set events
    carry the full list of instances for the coordinator to update its state.
    """
    event: EventType = Field(..., description="event type: add, del, set")
    instances: list[Instance] = Field(..., description="instances for coordinator")
