-- ============================================================================
-- MediArch PostgreSQL 数据库初始化脚本
-- 用于 LangChain 1.0 Checkpointer 和 Store 持久化
-- ============================================================================

-- 创建 Checkpointer 数据库（存储对话状态和 checkpoint）
CREATE DATABASE mediarch_checkpoints;

-- 创建 Store 数据库（存储用户长期记忆和偏好）
CREATE DATABASE mediarch_store;

-- 授予权限
GRANT ALL PRIVILEGES ON DATABASE mediarch_checkpoints TO postgres;
GRANT ALL PRIVILEGES ON DATABASE mediarch_store TO postgres;

-- 显示创建的数据库
\echo '✓ Database mediarch_checkpoints created'
\echo '✓ Database mediarch_store created'
\echo '✓ All privileges granted to postgres user'
