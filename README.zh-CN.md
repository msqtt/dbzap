# ⚡ dbzap

[English](README.md)

**连上数据库，CRUD 接口全有了。就这么简单。**

dbzap 读取你的数据库表结构，秒级生成 REST + GraphQL API —— 自带鉴权、文档和监控大盘。不写一行代码，不搞模板工程。

```
┌─────────┐        ┌─────────┐        ┌──────────────┐
│  数据库   │───────▶│  dbzap  │───────▶│   REST API   │
│  (DDL)   │        │         │        │  GraphQL API │
└─────────┘        └─────────┘        │  /auth       │
                                      │  /explorer   │
                                      │  /metrics    │
                                      │  /healthz    │
                                      └──────────────┘
```

## 三步上手

```bash
# 1. 安装
pip install dbzap        # 或者: poetry add dbzap

# 2. 配置
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost/mydb"
export JWT_SECRET_KEY="选一个强密码"

# 3. 启动
dbzap serve
```

搞定。数据库里每张表都自动有了完整的 CRUD 接口。

- **REST 文档**: `http://localhost:8000/docs`
- **GraphQL  playground**: `http://localhost:8000/graphql`
- **API 调试台**: `http://localhost:8000/explorer` （接口测试 + 监控大盘）
- **健康检查**: `http://localhost:8000/healthz`

## 功能一览

| 功能 | 说明 |
|------|------|
| REST CRUD | 每张表自动生成 `POST / GET / PUT / PATCH / DELETE`，Pydantic 模型自动推导 |
| GraphQL | 每张表自动生成 Query + Mutation + 类型定义 |
| JWT 鉴权 | 注册/登录，所有接口默认需要认证 |
| API 调试台 | 内置 Web UI，浏览、测试、调试你的接口 |
| 监控大盘 | 实时指标：请求速率、延迟、数据库连接池健康度 |
| 健康检查 | `/healthz` 存活探针 + 就绪探针，K8s 友好 |
| 指标导出 | Prometheus 兼容的 `/metrics` 端点 |

## 配置项

全部通过环境变量或 `.env` 文件配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | *必填* | `postgresql+asyncpg://...` |
| `JWT_SECRET_KEY` | *必填* | JWT 签名密钥 |
| `API_MODE` | `both` | `rest`、`graphql` 或 `both` |
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8000` | 监听端口 |
| `DB_POOL_SIZE` | `10` | 连接池大小 |
| `ENABLE_EXPLORER` | `true` | 是否启用 Web 调试台 |

## 本地开发

```bash
poetry install
cp .env.example .env     # 填入你的数据库连接信息
poetry run pytest
poetry run mypy src/
```

## 工作原理

```
启动 dbzap
  │
  ├─ 1. 连接数据库，读取 DDL（表、字段、类型、约束、外键）
  ├─ 2. SQL 类型 → Python 类型（确定性映射表）
  ├─ 3. 动态生成 FastAPI 路由 (REST) + Strawberry Schema (GraphQL)
  ├─ 4. 挂载 JWT 鉴权中间件，自动创建 _users 认证表
  └─ 5. 启动 uvicorn（连接池 + 指标采集 + 健康检查）
```

不需要写 ORM 模型，不需要跑迁移，不需要生成代码。

**数据库就是你的 Schema，dbzap 负责把 Schema 变成 API。**

## 许可证

MIT
