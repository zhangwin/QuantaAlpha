#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"
BACKEND_LOG="${PROJECT_ROOT}/backend.log"
FRONTEND_LOG="${PROJECT_ROOT}/frontend.log"

# =============================================================================
# 激活 conda 环境
# =============================================================================
eval "$(conda shell.bash hook)" 2>/dev/null
CONDA_ENV="${CONDA_ENV_NAME:-quantaalpha}"
conda activate "${CONDA_ENV}" 2>/dev/null
if [ $? -ne 0 ]; then
    source activate "${CONDA_ENV}" 2>/dev/null
fi
if ! python -c "import quantaalpha" 2>/dev/null; then
    echo "❌ 错误: quantaalpha 包未安装"
    echo "请先运行: conda activate ${CONDA_ENV} && cd ${PROJECT_ROOT} && pip install -e ."
    exit 1
fi
echo "✅ Python: $(python --version) (conda env: ${CONDA_ENV})"

# =============================================================================
# 加载 .env 配置
# =============================================================================
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
    echo "✅ 已加载 .env 配置"
else
    echo "⚠️  未找到 .env 文件，后端将使用默认配置"
fi

# =============================================================================
# 检测端口占用
# =============================================================================
BACKEND_PID=$(lsof -ti:8000 2>/dev/null)
FRONTEND_PID=$(lsof -ti:3000 2>/dev/null)

CONFIRM="y"
if [ -n "$BACKEND_PID" ] || [ -n "$FRONTEND_PID" ]; then
    echo ""
    [ -n "$BACKEND_PID" ] && echo "⚠️  后端端口 8000 被占用 (PID: $BACKEND_PID)"
    [ -n "$FRONTEND_PID" ] && echo "⚠️  前端端口 3000 被占用 (PID: $FRONTEND_PID)"
    read -p "是否关闭旧进程并重启? [y/N]: " CONFIRM
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        echo "已取消"
        exit 0
    fi
    if [ -n "$BACKEND_PID" ]; then
        echo "🛑 关闭后端进程 (PID: $BACKEND_PID)..."
        kill $BACKEND_PID 2>/dev/null; sleep 1
        kill -0 $BACKEND_PID 2>/dev/null && kill -9 $BACKEND_PID 2>/dev/null
    fi
    if [ -n "$FRONTEND_PID" ]; then
        echo "🛑 关闭前端进程 (PID: $FRONTEND_PID)..."
        kill $FRONTEND_PID 2>/dev/null; sleep 1
        kill -0 $FRONTEND_PID 2>/dev/null && kill -9 $FRONTEND_PID 2>/dev/null
    fi
    echo "✅ 已清理"
fi

# =============================================================================
# 启动后端
# =============================================================================
echo ""
echo "🔧 启动后端服务..."
cd "${PROJECT_ROOT}/frontend-v2"
nohup python backend/app.py >> "${BACKEND_LOG}" 2>&1 &
BACKEND_PID=$!

sleep 3
if curl -s --connect-timeout 2 http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "✅ 后端启动成功 (PID: $BACKEND_PID)"
else
    echo "❌ 后端启动失败，请查看日志: ${BACKEND_LOG}"
    kill $BACKEND_PID 2>/dev/null
    # 继续尝试启动前端，不退出
fi

# =============================================================================
# 启动前端
# =============================================================================
echo ""
echo "🔧 启动前端服务..."
if ! command -v node &> /dev/null; then
    echo "❌ 错误: 未找到 Node.js"
else
    cd "${PROJECT_ROOT}/frontend-v2"
    if [ ! -d "node_modules" ]; then
        echo "📦 安装前端依赖..."
        npm install >> "${FRONTEND_LOG}" 2>&1
        if [ $? -ne 0 ]; then
            echo "❌ 前端依赖安装失败，请查看日志: ${FRONTEND_LOG}"
        else
            echo "✅ 前端依赖安装完成"
        fi
    fi
    nohup npm run dev >> "${FRONTEND_LOG}" 2>&1 &
    FRONTEND_PID=$!
    sleep 3
    if curl -s --connect-timeout 2 http://localhost:3000 > /dev/null 2>&1; then
        echo "✅ 前端启动成功 (PID: $FRONTEND_PID)"
    else
        echo "❌ 前端启动失败，请查看日志: ${FRONTEND_LOG}"
    fi
fi

# =============================================================================
# 打印结果
# =============================================================================
echo ""
echo "============================================"
echo "✅ 服务启动完成!"
echo ""
echo "  前端:     http://localhost:3000"
echo "  后端 API: http://localhost:8000"
echo "  API 文档: http://localhost:8000/docs"
echo ""
echo "  后端日志: ${BACKEND_LOG}"
echo "  前端日志: ${FRONTEND_LOG}"
echo ""
echo "  停止: lsof -ti:8000 | xargs kill && lsof -ti:3000 | xargs kill"
echo "============================================"
