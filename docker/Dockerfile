# 1. 使用官方 CUDA 基础镜像
FROM python:3.10-slim

# 2. 设置容器内的工作目录
WORKDIR /workspace

# 3. 安装系统依赖（如果你的项目依赖一些系统库，比如 opencv 需要 libgl1）
# 如果不需要，这几行可以去掉
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 5. 安装 Python 依赖
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \ 
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple


# 6. 复制当前目录的所有代码到容器的 /app 目录
COPY . .
ENV PYTHONPATH=/workspace/pdf2train/src
CMD ["tail", "-f", "/dev/null"]
