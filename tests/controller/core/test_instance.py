from motor.resources.instance import Instance, ParallelConfig
from motor.resources.endpoint import Endpoint


def test_instance_active() -> None:
    parallel_config = ParallelConfig(dp=2, tp=2)
    pod_ip = "127.0.0.1"
    endpoints = {
        1: Endpoint(id=1, ip=pod_ip, port="1001"),
        2: Endpoint(id=2, ip=pod_ip, port="1002")
    }
    instance = Instance(
        job_name="test_active",
        model_name="test_model",
        id=1,
        role="prefill",
        parallel_config=parallel_config
    )
    instance.add_endpoints(pod_ip, endpoints)
    assert instance.get_endpoints_num() == len(endpoints)


def test_add_endpoints() -> None:
    parallel_config = ParallelConfig(dp=4, tp=2)
    pod_ip1 = "127.0.0.1"
    endpoints1 = {
        1: Endpoint(id=1, ip=pod_ip1, port="1001"),
        2: Endpoint(id=2, ip=pod_ip1, port="1002")
    }
    instance = Instance(
        job_name="test_add_endpoints",
        model_name="test_model",
        id=1,
        role="prefill",
        parallel_config=parallel_config
    )
    instance.add_endpoints(pod_ip1, endpoints1)


def test_del_endpoints() -> None:
    parallel_config = ParallelConfig(dp=2, tp=2)
    pod_ip = "127.0.0.1"
    endpoints = {
        1: Endpoint(id=1, ip=pod_ip, port="1001"),
        2: Endpoint(id=2, ip=pod_ip, port="1002")
    }
    instance = Instance(
        job_name="test_del_endpoints",
        model_name="test_model",
        id=1,
        role="prefill",
        parallel_config=parallel_config
    )
    instance.add_endpoints(pod_ip, endpoints)
    assert instance.get_endpoints_num() == len(endpoints)
    instance.del_endpoints(pod_ip)
    assert instance.get_endpoints_num() == 0