"""
Neo4j 配置一致性诊断工具

目的：
  诊断 verify_neo4j_data.py、build_kg、API 是否连接到同一个 Neo4j 实例

检查项：
1. Docker 容器状态（端口映射、数据卷）
2. .env 配置一致性
3. 各模块实际连接的 Neo4j 实例
4. 数据库名称一致性
5. 验证"同库事实"（seed 节点、索引等）
"""

import os
import sys
from pathlib import Path

# 添加项目根目录
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from neo4j import GraphDatabase

# 加载环境变量
load_dotenv()

def print_section(title):
    """打印章节标题"""
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)

def check_docker_neo4j():
    """检查 Docker Neo4j 容器状态"""
    print_section("1. Docker Neo4j 容器状态")

    import subprocess
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=neo4j", "--format", "{{.Names}}|{{.Status}}|{{.Ports}}"],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            print("[WARN] 无法执行 docker 命令，请确认 Docker 已安装并运行")
            return None

        lines = [line for line in result.stdout.strip().split('\n') if line]

        if not lines:
            print("[WARN] 未找到运行中的 Neo4j 容器")
            print("       请先运行: docker-compose up -d neo4j")
            return None

        for line in lines:
            parts = line.split('|')
            if len(parts) >= 3:
                name, status, ports = parts[0], parts[1], parts[2]
                print(f"[OK] 容器名称: {name}")
                print(f"[OK] 运行状态: {status}")
                print(f"[OK] 端口映射: {ports}")

                # 检查数据卷
                volume_result = subprocess.run(
                    ["docker", "inspect", name, "--format", "{{range .Mounts}}{{.Source}}->{{.Destination}}\n{{end}}"],
                    capture_output=True,
                    text=True,
                    check=False
                )

                if volume_result.returncode == 0:
                    print(f"\n[数据卷映射]")
                    for mount in volume_result.stdout.strip().split('\n'):
                        if mount and '/data' in mount:
                            print(f"  {mount}")
                            # 检查数据目录是否有内容
                            data_path = mount.split('->')[0]
                            if os.path.exists(data_path):
                                print(f"  [OK] 数据目录存在: {data_path}")
                                # 简单检查是否有 Neo4j 数据文件
                                databases_dir = os.path.join(data_path, 'databases')
                                if os.path.exists(databases_dir):
                                    print(f"  [OK] databases 目录存在，可能包含数据")
                                else:
                                    print(f"  [WARN] databases 目录不存在，可能是全新容器")
                            else:
                                print(f"  [WARN] 数据目录不存在: {data_path}")

                return name

    except FileNotFoundError:
        print("[WARN] 未找到 docker 命令，无法检查容器状态")
        return None
    except Exception as e:
        print(f"[FAIL] 检查 Docker 容器失败: {e}")
        return None

def check_env_config():
    """检查环境变量配置"""
    print_section("2. 环境变量配置")

    # 读取所有 .env 文件
    env_files = [
        Path(project_root.parent) / ".env",
        Path(project_root.parent) / ".env.minimal"
    ]

    configs = {}
    for env_file in env_files:
        if env_file.exists():
            print(f"\n[检查] {env_file.name}")
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        if key.startswith('NEO4J_'):
                            if env_file.name not in configs:
                                configs[env_file.name] = {}
                            configs[env_file.name][key] = value

    # 对比配置
    if configs:
        keys = set()
        for file_config in configs.values():
            keys.update(file_config.keys())

        print(f"\n[配置对比]")
        print(f"{'配置项':<20} {'.env':<30} {'.env.minimal':<30}")
        print("-" * 80)

        for key in sorted(keys):
            env_val = configs.get('.env', {}).get(key, '(未设置)')
            minimal_val = configs.get('.env.minimal', {}).get(key, '(未设置)')

            match = "[OK]" if env_val == minimal_val else "[DIFF]"
            print(f"{key:<20} {env_val:<30} {minimal_val:<30} {match}")

    # 显示当前环境变量（实际生效的）
    print(f"\n[当前环境变量实际值]")
    neo4j_vars = ['NEO4J_URI', 'NEO4J_USER', 'NEO4J_PASSWORD', 'NEO4J_DATABASE']
    for var in neo4j_vars:
        val = os.getenv(var, '(未设置)')
        # 隐藏密码
        if 'PASSWORD' in var and val != '(未设置)':
            val = '*' * len(val)
        print(f"  {var:<20} = {val}")

def check_neo4j_connection():
    """检查 Neo4j 连接并验证数据库信息"""
    print_section("3. Neo4j 连接测试与数据库验证")

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE")

    print(f"[连接] URI: {uri}")
    print(f"[连接] User: {user}")
    print(f"[连接] Password: {'*' * len(password)}")
    print(f"[连接] Database: {database or '(默认)'}")

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        print("[OK] Neo4j 连接成功\n")

        # 查询服务器信息
        with driver.session() as session:
            # 获取数据库列表
            try:
                result = session.run("SHOW DATABASES")
                databases = [record["name"] for record in result]
                print(f"[可用数据库] {', '.join(databases)}")
            except Exception:
                print(f"[INFO] 无法列出数据库（可能是社区版）")

            # 获取当前数据库名
            try:
                result = session.run("CALL dbms.components() YIELD name, versions, edition")
                for record in result:
                    print(f"[Neo4j 版本] {record['name']} {record['versions'][0]} ({record['edition']})")
            except Exception as e:
                print(f"[WARN] 无法获取版本信息: {e}")

            # 查询节点统计
            result = session.run("MATCH (n) RETURN count(n) as total")
            total_nodes = result.single()['total']
            print(f"\n[节点总数] {total_nodes:,}")

            if total_nodes == 0:
                print("[WARN] 数据库为空！")
                print("       建议运行: python -m backend.cli.build_kg")
                return False

            # 查询 seed 节点（验证"同库事实"）
            result = session.run("""
                MATCH (n)
                WHERE n.id STARTS WITH 'seed_' OR n.name CONTAINS '种子' OR n.is_seed = true
                RETURN count(n) as seed_count
            """)
            seed_count = result.single()['seed_count']
            print(f"[Seed 节点] {seed_count} 个")

            if seed_count > 0:
                print(f"  [OK] 找到 seed 节点，说明可能已完成初始化")
            else:
                print(f"  [WARN] 未找到 seed 节点，可能未运行预注入")

            # 查询索引
            try:
                result = session.run("SHOW INDEXES")
                indexes = [record["name"] for record in result]
                print(f"\n[索引列表] 共 {len(indexes)} 个")
                for idx in indexes[:5]:  # 只显示前5个
                    print(f"  - {idx}")
                if len(indexes) > 5:
                    print(f"  ... 还有 {len(indexes) - 5} 个")
            except Exception as e:
                print(f"[WARN] 无法列出索引: {e}")

            return True

        driver.close()

    except Exception as e:
        print(f"[FAIL] Neo4j 连接失败: {e}")
        print("\n可能的原因:")
        print("  1. Neo4j 容器未启动")
        print("  2. 端口配置错误")
        print("  3. 用户名/密码错误")
        print("  4. 连接到了错误的实例（Desktop vs Docker）")
        return False

def check_code_consistency():
    """检查代码中的 Neo4j 连接方式"""
    print_section("4. 代码连接方式检查")

    files_to_check = [
        ("verify_neo4j_data.py", project_root / "cli" / "verify_neo4j_data.py"),
        ("kg_builder.py", project_root / "databases" / "graph" / "builders" / "kg_builder.py"),
        ("neo4j_connector.py", project_root / "app" / "services" / "neo4j_connector.py"),
    ]

    for name, path in files_to_check:
        print(f"\n[检查] {name}")
        if not path.exists():
            print(f"  [SKIP] 文件不存在")
            continue

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

            # 检查是否使用环境变量
            uses_neo4j_uri = 'NEO4J_URI' in content
            uses_neo4j_database = 'NEO4J_DATABASE' in content
            uses_driver = 'GraphDatabase.driver' in content
            uses_session_database = 'database=' in content or 'database =' in content

            print(f"  [读取] NEO4J_URI: {'[OK]' if uses_neo4j_uri else '[MISS]'}")
            print(f"  [读取] NEO4J_DATABASE: {'[OK]' if uses_neo4j_database else '[MISS]'}")
            print(f"  [创建] GraphDatabase.driver: {'[OK]' if uses_driver else '[MISS]'}")
            print(f"  [指定] database 参数: {'[OK]' if uses_session_database else '[MISS]'}")

            if uses_neo4j_uri and not uses_session_database:
                print(f"  [WARN] 未显式指定 database 参数，可能使用默认库")

def generate_recommendations():
    """生成修复建议"""
    print_section("5. 诊断建议")

    print("""
基于诊断结果，以下是常见问题的修复建议：

[A] 如果容器未启动或端口映射错误
    cd e:\\MyPrograms\\250804-MediArch System
    docker-compose down
    docker-compose up -d neo4j
    docker logs -f mediarch-neo4j  # 查看启动日志

[B] 如果 .env 和 .env.minimal 配置不一致
    统一两个文件的 Neo4j 配置：
      NEO4J_URI=bolt://localhost:7687
      NEO4J_USER=neo4j
      NEO4J_PASSWORD=mediarch2024
      NEO4J_DATABASE=neo4j

[C] 如果数据库为空（节点数为 0）
    python -m backend.cli.build_kg

[D] 如果怀疑连接到了 Neo4j Desktop（而非 Docker）
    1. 关闭 Neo4j Desktop
    2. 确认 Docker 容器占用 7687 端口:
       docker ps | findstr neo4j
    3. 重启 API 和所有脚本

[E] 如果数据卷指向变化
    检查 docker-compose.yml 第113行:
      volumes:
        - E:/MediArch-Data/neo4j:/data
    确认该路径是否正确，且包含你之前的数据

[F] 如果 verify、build_kg、API 连接不一致
    确保所有模块都加载了相同的 .env 文件:
      from dotenv import load_dotenv
      load_dotenv()  # 默认加载项目根目录的 .env
    """)

def main():
    print()
    print("*" * 70)
    print("*" + " " * 20 + "Neo4j 配置一致性诊断工具" + " " * 19 + "*")
    print("*" * 70)

    # 执行所有检查
    check_docker_neo4j()
    check_env_config()
    has_data = check_neo4j_connection()
    check_code_consistency()
    generate_recommendations()

    print()
    print("=" * 70)
    print("  诊断完成！")
    print("=" * 70)
    print()

    if not has_data:
        print("[总结] Neo4j 连接成功，但数据库为空")
        print("       下一步: python -m backend.cli.build_kg")
    else:
        print("[总结] Neo4j 连接成功，且已有数据")
        print("       如果前端仍显示空，请检查 API 和前端的连接配置")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[WARN] 用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n[FAIL] 诊断失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
