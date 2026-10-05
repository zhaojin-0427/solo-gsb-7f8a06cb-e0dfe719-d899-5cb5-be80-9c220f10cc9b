# IPv6 Migration Planner API

基于 Python / FastAPI 的 IPv6 前缀迁移规划服务。给定父前缀 `pool`（`/123`～`/127`）、
已有前缀分配 `existing` 和目标列表 `targets`，服务会为所有目标计算互不重叠、按网络
地址对齐且位于 pool 内的新前缀；未列入目标的旧租户视为释放。

## 启动

需要 Docker 与 Docker Compose 插件：

```bash
docker compose up --build
```

服务监听在 <http://localhost:8000>：

- API：`POST http://localhost:8000/plan`
- 健康检查：`GET http://localhost:8000/health`
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>

无需任何外部服务或数据库。

## 请求

`POST /plan`，JSON 请求体：

| 字段 | 说明 |
| --- | --- |
| `pool` | 父前缀，长度必须在 `/123`～`/127` 之间，按网络地址书写 |
| `existing` | 已有分配，最多 8 条；`id` 为非空 ASCII 字符串且互不相同，`prefix` 为对齐的 IPv6 前缀且互不重叠、位于 pool 内 |
| `targets` | 目标，1～8 条；`id` 为非空 ASCII 字符串且互不相同，`prefix_length` 为整数，严格大于 pool 长度且不超过 128 |

示例：

```bash
curl -s http://localhost:8000/plan \
  -H 'Content-Type: application/json' \
  -d '{
    "pool": "2001:db8::/125",
    "existing": [
      {"id": "a", "prefix": "2001:db8::/127"},
      {"id": "b", "prefix": "2001:db8::2/127"}
    ],
    "targets": [
      {"id": "a", "prefix_length": 126},
      {"id": "b", "prefix_length": 127},
      {"id": "c", "prefix_length": 127}
    ]
  }'
```

响应：

```json
{
  "allocations": [
    {"id": "a", "prefix": "2001:db8::4/126"},
    {"id": "b", "prefix": "2001:db8::2/127"},
    {"id": "c", "prefix": "2001:db8::/127"}
  ]
}
```

分配按 `id` 排序。

## 优化目标（按优先级）

1. 最大化旧租户原前缀**完全不变**（网络地址与长度均相同；长度改变不算不变）的数量；
2. 在 1 的前提下，最小化旧租户**首地址（网络地址）发生改变**的数量；
3. 在前两者的前提下，按 `id` 顺序取新前缀整数网络地址序列字典序最小的方案。

只有完整方案才会返回；若不存在可行方案，返回 `409`，且不包含任何部分结果。

## 状态码

- `200`：成功，返回完整分配；
- `422`：非法输入，响应体 `detail` 中每个错误都带有可定位字段的 `loc`
  （如 `["body", "targets", 0, "prefix_length"]`）和说明；
- `409`：不存在满足条件的完整分配方案。

## 本地开发（不使用 Docker）

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

运行测试：

```bash
pip install -r requirements.txt pytest httpx
pytest
```
