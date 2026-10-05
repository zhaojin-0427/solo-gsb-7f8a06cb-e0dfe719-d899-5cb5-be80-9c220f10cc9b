# IPv6 Migration Planner

一个基于 FastAPI 的 IPv6 前缀迁移规划服务。给定一个父前缀池、一批已有分配和
一组目标需求，计算一份完整的、互不重叠且按网络地址对齐的新分配方案。

## 运行

```bash
docker compose up --build
```

服务启动后监听 **http://localhost:8000**，交互式文档见
http://localhost:8000/docs 。无需任何外部服务。

本地开发（不用 Docker）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API

### `POST /plan`

请求体：

```json
{
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
}
```

字段约束（违反任意一条均返回 **422**，响应 `detail[].loc` 可定位到具体字段）：

- `pool`：IPv6 父前缀，长度须在 **/123 ~ /127** 之间，且按网络地址对齐；
- `existing`：至多 **8** 条；`id` 唯一、非空、纯 ASCII；`prefix` 必须对齐、
  位于 `pool` 内且两两互不重叠；
- `targets`：**1 ~ 8** 条；`id` 唯一、非空、纯 ASCII；`prefix_length` 为整数，
  须介于 pool 前缀长度与 **/128** 之间；
- 未出现在 `targets` 中的旧分配视为**已释放**，其地址空间可重新分配。

成功时返回 **200**，按 `id` 排序的分配列表：

```json
[
  {"id": "a", "prefix": "2001:db8::4/126"},
  {"id": "b", "prefix": "2001:db8::2/127"},
  {"id": "c", "prefix": "2001:db8::/127"}
]
```

当不存在覆盖全部目标的完整方案时返回 **409**，且不返回任何部分结果。

### 方案择优规则

存在多个完整方案时，依次按以下规则选择：

1. **最大化**旧租户（同时出现在 `existing` 与 `targets` 中的 id）新前缀与
   原前缀**完全不变**的数量（长度改变不算不变）；
2. **最小化**旧租户**首地址改变**的数量（仅长度变化、首地址不变者不受影响）；
3. 按 `id` 顺序取新前缀整数网络地址序列**字典序最小**者。

例如上文请求中：`b` 保持 `2001:db8::2/127` 不变，`a` 扩容为 /126 后被安置到
`2001:db8::4/126`，新租户 `c` 获得 `2001:db8::/127`。

## 测试

```bash
pip install pytest httpx
pytest tests/
```

## 项目结构

```
app/
  main.py     # FastAPI 入口与 /plan 路由
  models.py   # 请求模型与全部 422 校验
  solver.py   # 分配求解器（位掩码搜索 + 三条择优规则）
tests/
  test_plan.py
Dockerfile
compose.yaml
requirements.txt
```
