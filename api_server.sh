#!/bin/bash
if [ -f ".env" ]; then
    API_PORT=$(python -c "
import os
from dotenv import load_dotenv
load_dotenv()
print(os.getenv('API_PORT', '9039'))
")
    CONDA_ENV_PATH=$(python -c "
import os
from dotenv import load_dotenv
load_dotenv()
print(os.getenv('CONDA_ENV_PATH', '/work/soft/anaconda3/bin/'))
")
    CONDA_ENVIRONMENT=$(python -c "
import os
from dotenv import load_dotenv
load_dotenv()
print(os.getenv('CONDA_ENVIRONMENT', 'pdf2train'))
")
else
    API_PORT=9039
    CONDA_ENV_PATH='/work/soft/anaconda3/bin/'
    CONDA_ENVIRONMENT='pdf2train'
fi

# --- 2. 获取项目路径 ---
if [ -n "$1" ]; then
    PROJECT_ROOT="$1"
else
    PROJECT_ROOT=$(dirname "$(readlink -f "$0")")
fi

# --- 3. 安全的端口清理 ---
check_and_kill_port() {
    local port=$1
    # 安全检查：确保端口不是空的，防止误杀
    if [ -z "$port" ]; then
        echo "错误: 端口未定义，跳过清理。"
        return
    fi
    
    local pid=$(lsof -t -i :$port)

    if [ -n "$pid" ]; then
        echo "端口 $port 已被占用 (PID: $pid)，正在清理..."
        kill -9 $pid
    else
        echo "端口 $port 空闲。"
    fi
}

check_and_kill_port $API_PORT

# 激活虚拟环境
source "$CONDA_ENV_PATH/../etc/profile.d/conda.sh" 2>/dev/null || source "/work/soft/anaconda3/etc/profile.d/conda.sh"
echo "正在激活环境: $CONDA_ENVIRONMENT"
conda activate $CONDA_ENVIRONMENT

# --- 5. 准备日志 ---
timestamp=$(date +"%Y%m%d%H%M%S")
LOG_PATH=$PROJECT_ROOT/logs/api_server
LOG_FILE="$LOG_PATH/${timestamp}.log"

if [ ! -d "$LOG_PATH" ]; then
    # 日志目录不存在，创建它
    mkdir -p "$LOG_PATH"
fi
python -m api.table.init_tables
echo "日志文件路径: $LOG_FILE"
cd "$PROJECT_ROOT" || { echo "无法切换到项目目录: $PROJECT_ROOT"; exit 1; }
nohup python -m api.server.main_server -p $API_PORT > "$LOG_FILE" 2>&1 &
echo "api_server服务已在后台运行，输出日志位于: $LOG_FILE"