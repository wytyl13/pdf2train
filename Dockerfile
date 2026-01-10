# 1. 使用官方 CUDA 基础镜像
FROM nvidia/cuda:12.6.3-cudnn-devel-ubuntu22.04

# 2. 设置容器内的工作目录
WORKDIR /app

# 3. 安装系统依赖（如果你的项目依赖一些系统库，比如 opencv 需要 libgl1）
# 如果不需要，这几行可以去掉
RUN sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    sed -i 's/security.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y git python3 python3-pip && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip && \
    pip install --no-cache-dir --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple && \
    rm -rf /var/lib/apt/lists/*

# 4. 复制依赖文件到容器
COPY agent-weiyutao.zip /app/agent-weiyutao.zip
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install /app/agent-weiyutao.zip -i https://pypi.tuna.tsinghua.edu.cn/simple

# 5. 安装 Python 依赖
# --no-cache-dir 可以减小镜像体积
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \ 
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

RUN --mount=type=cache,target=/root/.cache/pip \ 
    pip install tabulate lxml html5lib -i https://pypi.tuna.tsinghua.edu.cn/simple

# 6. 复制当前目录的所有代码到容器的 /app 目录
COPY . .

# 7. 声明启动命令 (根据你的实际启动文件修改，比如 main.py 或 app.py)
# 假设你的入口文件是 main.py
CMD ["sh", "-c", "python -m api.table.init_tables -n && python -m api.server.main_server -p 8000"]
