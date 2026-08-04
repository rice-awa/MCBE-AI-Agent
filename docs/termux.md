# Termux 部署指南

在 Android Termux 环境部署 MCBE AI Agent 的完整指南，合并自原 README 的部署、常见问题与优化建议。

## 部署

### 1. 准备工作

```bash
# 更换清华源（可选）
termux-change-repo

# 更新包管理器
pkg update && pkg upgrade -y

# 安装基础工具
pkg install python git wget curl -y
```

### 2. 获取项目

```bash
# 克隆项目（如无法使用 git 克隆，可直接下载压缩包到本地解压使用）
git clone https://github.com/rice-awa/MCBE-AI-Agent
cd MCBE-AI-Agent

# 创建虚拟环境
python -m venv venv
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. Termux 特定配置

```json
// 1. 确保 config.json 中主机设置为 0.0.0.0 而不是 localhost
{
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  }
}
```

```bash
# 2. 获取 Termux 的 IP 地址
ifconfig | grep inet

# 3. 确保 Termux 可以监听端口（可能需要允许 Termux 的网络访问权限）
```

### 5. 启动服务

```bash
# 前台启动
python cli.py serve

# 或使用 tmux 后台运行
pkg install tmux -y
tmux new -s mcbe_agent
source venv/bin/activate
python cli.py serve
# 按 Ctrl+B 然后按 D 分离会话；tmux attach -t mcbe_agent 重新连接
```

### 6. Minecraft 连接

在 MCBE 中使用 Termux 的 IP 地址或本地回环地址：

```
/wsserver localhost:8080
```

## 常见问题

### 1. 端口无法访问

```bash
# 检查 Termux 是否具有必要权限
termux-setup-storage

# 使用 ngrok 绕过防火墙（或将服务置于同一局域网）
ngrok http 8080
```

### 2. Python 包安装失败

```bash
# 更新 pip 和 setuptools
pip install --upgrade pip setuptools wheel

# 使用清华源加速
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 预编译包（部分包在 Android 上无 wheel 时可尝试）
pip install --prefer-binary -r requirements.txt
```

### 3. 内存不足

```json
// config.json：降低 Worker 数量与队列
{
  "queue": {
    "llm_worker_count": 1,
    "max_size": 50
  }
}
```

```bash
# 优化虚拟内存
pkg install tur-repo -y
pkg install zram -y
```

### 4. 后台运行

**tmux**:

```bash
pkg install tmux -y
tmux new -s mcbe_agent
cd ~/MCBE-AI-Agent && source venv/bin/activate && python cli.py serve
# 分离: Ctrl+B 然后 D；重连: tmux attach -t mcbe_agent
```

**nohup**:

```bash
nohup python cli.py serve > mcbe.log 2>&1 &
```

### 5. 连接失败排查

```bash
# 检查端口监听
netstat -tulpn | grep 8080

# 测试本地连接
curl http://localhost:8080/health
```

## 优化建议

### 1. 网络配置（跨网络访问）

```bash
# 使用 zerotier 创建虚拟局域网
pkg install zerotier-one -y
zerotier-one -d
zerotier-cli join <network_id>

# 或使用 tailscale
pkg install tailscale -y
tailscale up
```

### 2. 性能优化

```bash
# 安装性能监控工具
pkg install htop proot-distro -y

# 使用轻量级系统（可选）
proot-distro install ubuntu
proot-distro login ubuntu
```

### 3. 存储优化

```bash
# 清理缓存
pkg clean
pip cache purge

# 使用外部存储
termux-setup-storage
ln -s /storage/emulated/0/Download/mcbe_data ./data
```

### 4. 自动化脚本

创建 `termux_start.sh`:

```bash
#!/data/data/com.termux/files/usr/bin/bash

# 激活虚拟环境
source ~/MCBE-AI-Agent/venv/bin/activate

# 启动服务
cd ~/MCBE-AI-Agent
python cli.py serve
```

```bash
chmod +x termux_start.sh
```

### 5. Termux 特定安全

- 定期更新 Termux 包
- 使用强密码保护设备
- 仅在有需要时开放端口
