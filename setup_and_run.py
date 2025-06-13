#!/usr/bin/env python3
"""
LiteLLM 完整启动脚本 - 使用.env文件配置
"""
import os
import sys
import subprocess
import uvicorn
from dotenv import load_dotenv

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# 加载.env文件中的环境变量
load_dotenv()

print(f"🔗 数据库连接: {os.getenv('DATABASE_URL', '未设置')}")

def setup_prisma():
    """初始化Prisma"""
    print("🔧 正在初始化Prisma...")
    
    try:
        # 检查prisma是否已安装
        result = subprocess.run(['prisma', '--version'], capture_output=True, text=True)
        if result.returncode != 0:
            print("❌ Prisma未安装，请运行: pip install prisma")
            return False
            
        print("✅ Prisma已安装")
        
        # 生成Prisma客户端
        print("🔨 生成Prisma客户端...")
        result = subprocess.run(['prisma', 'generate'], cwd=project_root)
        if result.returncode != 0:
            print("❌ Prisma客户端生成失败")
            return False
            
        print("✅ Prisma客户端生成成功")
        
        # 推送数据库模式
        print("📦 推送数据库模式...")
        result = subprocess.run(['prisma', 'db', 'push'], cwd=project_root)
        if result.returncode != 0:
            print("⚠️  数据库模式推送失败，可能需要手动处理")
        else:
            print("✅ 数据库模式推送成功")
            
        return True
        
    except FileNotFoundError:
        print("❌ 找不到Prisma命令，请确保已安装: pip install prisma")
        return False
    except Exception as e:
        print(f"❌ Prisma初始化出错: {e}")
        return False

if __name__ == "__main__":
    # 检查是否有DATABASE_URL环境变量
    if not os.getenv('DATABASE_URL'):
        print("❌ 未找到DATABASE_URL环境变量！")
        sys.exit(1)
    
    # 初始化Prisma
    if not setup_prisma():
        print("❌ Prisma初始化失败，请手动安装和配置")
        sys.exit(1)
    
    # 启动服务
    print("🚀 启动LiteLLM服务...")
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', 4000))
    
    try:
        uvicorn.run(
            "litellm.proxy.proxy_server:app",
            host=host,
            port=port,
            reload=True,
            log_level="debug",
            workers=1,
        )
    except Exception as e:
        print(f"❌ 服务启动失败: {e}")
        print("💡 提示：请确保数据库可连接，并检查端口是否被占用") 