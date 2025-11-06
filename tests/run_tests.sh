#!/bin/bash

# 函数：显示帮助信息
show_help() {
    echo "用法: $0 [选项] [测试用例路径]"
    echo "选项:"
    echo "  -h, --help        显示此帮助信息"
    echo "  -v, --verbose     显示详细的测试输出"
    echo "  -s                显示测试的输出内容"
    echo "  -x                遇到失败时立即停止"
    echo "  --cov            启用代码覆盖率统计"
    echo "  --cov-report=    指定覆盖率报告格式(term/html/xml)"
    echo "  --exclude=       排除指定文件/目录不统计覆盖率(支持通配符)"
    echo "  -k EXPRESSION    只运行匹配表达式的测试"
    echo ""
    echo "默认排除规则:"
    echo "  - 测试文件: */tests/*"
    echo "  - 缓存文件: */__pycache__/*"
    echo "  - gRPC生成文件: */cluster_grpc/*_pb2*.py, */cluster_grpc/*_grpc.py"
    echo ""
    echo "示例:"
    echo "  $0 --cov --cov-report=html tests/controller/"
    echo "  $0 --cov --cov-report=html tests/coordinator/"
    echo "  $0 -v -k 'test_register'"
    echo "  $0 --cov --exclude='motor/config/*' --exclude='motor/utils/logger.py'"
}

# 确保在项目根目录
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# 清理之前的覆盖率数据
rm -f .coverage
rm -rf htmlcov

# 初始化变量
PYTEST_ARGS=""
COVERAGE_ENABLED=false
COVERAGE_REPORT="term"
COVERAGE_EXCLUDE=()

# 默认排除的文件/目录（生成的文件、测试文件等）
DEFAULT_EXCLUDES=(
    "*/tests/*"
    "*/__pycache__/*"
    "*/cluster_grpc/*_pb2*.py"
    "*/cluster_grpc/*_grpc.py"
)

# 设置 PYTHONPATH
export PYTHONPATH="$ROOT_DIR:$PYTHONPATH"
export PYTHONPATH="$ROOT_DIR/motor:$PYTHONPATH"

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        --cov)
            COVERAGE_ENABLED=true
            shift
            ;;
        --cov-report=*)
            COVERAGE_REPORT="${1#*=}"
            shift
            ;;
        --exclude=*)
            COVERAGE_EXCLUDE+=("${1#*=}")
            shift
            ;;
        *)
            PYTEST_ARGS="$PYTEST_ARGS $1"
            shift
            ;;
    esac
done

# 检查必要的包是否安装
check_dependencies() {
    echo "检查测试依赖..."
    
    # 检查 pytest 相关包
    python3 -c "import pytest" 2>/dev/null || { echo "安装 pytest..."; pip install pytest; }
    python3 -c "import pytest_cov" 2>/dev/null || { echo "安装 pytest-cov..."; pip install pytest-cov; }
    
    # 检查项目核心依赖
    echo "检查项目核心依赖..."
    python3 -c "import fastapi" 2>/dev/null || { echo "安装 fastapi..."; pip install fastapi>=0.68.0; }
    python3 -c "import uvicorn" 2>/dev/null || { echo "安装 uvicorn..."; pip install "uvicorn[standard]>=0.15.0"; }
    python3 -c "import grpc" 2>/dev/null || { echo "安装 grpcio..."; pip install grpcio>=1.40.0; }
    python3 -c "import grpc_tools" 2>/dev/null || { echo "安装 grpcio-tools..."; pip install grpcio-tools>=1.40.0; }
    python3 -c "import pydantic" 2>/dev/null || { echo "安装 pydantic..."; pip install pydantic>=1.8.0; }
    python3 -c "from OpenSSL import crypto" 2>/dev/null || { echo "安装 pyOpenSSL..."; pip install pyOpenSSL>=21.0.0; }
    
    # 检查 HTTP 客户端库
    echo "检查 HTTP 客户端依赖..."
    python3 -c "import requests" 2>/dev/null || { echo "安装 requests..."; pip install requests>=2.25.0; }
    python3 -c "import httpx" 2>/dev/null || { echo "安装 httpx..."; pip install httpx>=0.24.0; }
    
    # 检查其他可能需要的测试依赖
    python3 -c "import asyncio" 2>/dev/null || { echo "asyncio 不可用，这可能会影响异步测试"; }
    python3 -c "import tempfile" 2>/dev/null || { echo "tempfile 不可用，这可能会影响临时文件测试"; }
    
    echo "依赖检查完成"
}

# 确保依赖包已安装
check_dependencies

# 构建pytest命令
CMD="pytest"

# 如果启用了覆盖率统计
if [ "$COVERAGE_ENABLED" = true ]; then
    # 创建临时的 .coveragerc 配置文件
    COVERAGERC_FILE=".coveragerc.tmp"
    cat > "$COVERAGERC_FILE" << EOF
[run]
source = motor
omit = 
EOF
    
    # 添加排除文件的配置（默认排除规则 + 用户指定规则）
    for exclude_pattern in "${DEFAULT_EXCLUDES[@]}"; do
        echo "    $exclude_pattern" >> "$COVERAGERC_FILE"
    done
    
    if [ ${#COVERAGE_EXCLUDE[@]} -gt 0 ]; then
        for exclude_pattern in "${COVERAGE_EXCLUDE[@]}"; do
            echo "    $exclude_pattern" >> "$COVERAGERC_FILE"
        done
    fi
    
    # 指定源代码路径和测试路径
    CMD="$CMD --cov=motor --cov-report=$COVERAGE_REPORT --cov-config=$COVERAGERC_FILE"
fi

# 添加其他pytest参数
if [ ! -z "$PYTEST_ARGS" ]; then
    CMD="$CMD $PYTEST_ARGS"
else
    # 如果没有指定测试路径，默认运行所有测试
    CMD="$CMD tests/"
fi

# 执行测试命令
echo "执行命令: $CMD"
$CMD

# 清理临时文件
if [ -f ".coveragerc.tmp" ]; then
    rm -f ".coveragerc.tmp"
fi