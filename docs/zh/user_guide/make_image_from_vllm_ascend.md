# 镜像制作

## 1、获取vLLM-Ascend发布的镜像版本

获取方法打开[RED HAT](https://quay.io/repository/ascend/vllm-ascend?tab=tags)点击需要下载的版本。
以v0.13.0版本为例，下载命令为：

```
docker pull quay.io/ascend/vllm-ascend:v0.13.0
```

> 为提高下载速度，可讲quay.io替换为quay.nju.edu.cn

## 2、在镜像中安装PyMotor

准备好目标PyMotor代码，执行以下命令，git命令根据需要下载的分支或tag进行修改：

```
cd /mnt/
git clone <PyMotor的git链接>
```

通过`docker images`命令查看第一步下载下来的镜像。
通过以下命令运行容器并挂载mnt目录：

```
docker run -d --name docker-vllm-ascend -v /mnt/:/mnt/ <镜像名称>
```

制作镜像：

```
docker exec -it docker-vllm-ascend bash

cd /mnt/MindIE-pyMotor

pip install -r requirements.txt

bash build.sh

pip install --force-reinstall ./dist/motor-0.1.0-py3-none-any.whl

exit
```

保存镜像：

```
docker commit -m "add PyMotor"  docker-vllm-ascend  mindie-motor-vllm:dev-800I-A3-py311-lts-aarch64
```

保存后，`mindie-motor-vllm:dev-800I-A3-py311-lts-aarch64`镜像就是制作好之后带PyMotor的镜像。