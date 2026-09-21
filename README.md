# 三掩模版图分配工作台

面向先进制程版图分解的全栈工作台：把相互冲突的版图片段分配到三张掩模，
并精确最小化被切开的缝合边权重总和（降低套刻风险）。React 前端 +
FastAPI 后端，通过真实 API 求解，可在浏览器中复核掩模分配与每一条被切开的缝合边。

## 功能

- 在浏览器中粘贴/编辑片段编号、冲突边、带正整数权重的缝合边
- 精确求解（MILP，CBC）：冲突边两端必异色，缝合边异色时计入权重，目标为切开权重总和最小
- 颜色规范化：按片段编号升序扫描，首次出现的颜色依次命名为掩模 1/2/3，消除 6 种置换对称
- 唯一性判定：判断规范化后的最优解是否唯一；多解时返回字典序最小方案及另一份不同见证
- 输入错误逐项展示并清除旧结果；无解（冲突图不可三染色）与输入错误明确区分
- 逐条列出被切开的缝合边及其权重、两端掩模

## 快速开始（Docker）

```bash
docker compose up --build        # 默认 http://localhost:8000
HOST_PORT=9000 docker compose up --build   # 自定义宿主机端口
```

也可以复制 `.env.example` 为 `.env` 并修改 `HOST_PORT`。

Compose 包含两个服务：

- `app`：应用本体，带健康检查（轮询 `/api/health`）
- `verify`：验收服务，等 `app` 健康后对真实 API 跑验收套件，全部通过则以 0 退出

```bash
docker compose up --build --abort-on-container-exit   # verify 结果即验收结果
docker compose logs verify
```

## 验收

仓库根目录的 `verify` 是可执行验收入口（仅依赖 Python 标准库）：

```bash
./verify                       # 对 http://localhost:${HOST_PORT:-8000} 运行验收
HOST_PORT=9000 ./verify
./verify --base-url http://app:8000
```

验收内容：健康检查、前端页面可达、唯一最优解与规范化、多解时的字典序最小
方案与不同见证、带权实例与暴力枚举 oracle 逐项对照（目标值/唯一性/字典序/
见证/切开边集合）、无解情形的区分、输入错误逐项返回。

## 本地开发

```bash
# 后端（http://localhost:8000）
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
cd backend && ../.venv/bin/uvicorn app.main:app --reload

# 前端（http://localhost:5173，/api 代理到 8000）
cd frontend && npm install && npm run dev

# 测试
cd backend && ../.venv/bin/python -m pytest tests -q
```

前端构建产物由 Docker 多阶段构建自动生成并交由 FastAPI 静态托管。

## API

### `GET /api/health`

返回 `{"status": "ok"}`。

### `POST /api/solve`

请求体：

```json
{
  "fragments": [1, 2, 3, 4],
  "conflict_edges": [[1, 2], [2, 3], [1, 3]],
  "stitch_edges": [{"pair": [3, 4], "weight": 5}, [1, 4, 1]]
}
```

- `fragments`：4–48 个唯一整数片段编号（必填）
- `conflict_edges`：二元数组列表（可缺省/为空）
- `stitch_edges`：`{"pair": [A, B], "weight": W}` 或 `[A, B, W]`（可缺省/为空）

最优响应（HTTP 200）：

```json
{
  "status": "optimal",
  "objective": 1,
  "unique": true,
  "assignment": {"1": 0, "2": 1, "3": 2, "4": 2},
  "cut_stitches": [{"pair": [1, 4], "weight": 1}],
  "witness": null
}
```

- `assignment`：片段编号 → 掩模（0/1/2，已按首次出现规范化）
- `unique`：规范化后的最优解是否唯一
- `witness`：多解时给出另一份不同的规范化最优方案（含其切开边），唯一时为 `null`
- 无解时返回 `{"status": "infeasible"}`（HTTP 200）

输入非法（HTTP 400，逐项列出全部错误）：

```json
{
  "status": "invalid",
  "errors": [{"loc": "stitch_edges[0]", "message": "缝合权重必须是正整数，收到 0"}]
}
```

## 模型与校验规则

- 4–48 个唯一整数片段编号
- 冲突边两端必须异色；缝合边两端异色时计入其正整数权重
- 同一无向点对：不得重复出现、不得同时属于两类边、不得形成自环
- 边的两个端点都必须出现在片段列表中
- 所有校验错误一次性逐项返回，前端展示并清除旧结果

## 求解方法

后端将问题建模为 0-1 规划（PuLP + CBC，精确求解）：

- `x[i][k] ∈ {0,1}`：片段 i 是否着 mask k；每片恰一色
- 规范化约束：`x[i][k] ≤ Σ_{j<i} x[j][k-1]`（k>0），强制颜色按片段编号升序首次出现
- 冲突边：两端同色的 one-hot 变量两两互斥
- 缝合边：切开变量 `y ≥ x[u][k] − x[v][k]`，目标最小化 `Σ w·y`
- 求得最优值后固定最优面，逐位最小化各片段颜色得到字典序最小方案；
  再加排除该方案的割平面重解，判定唯一性并取回不同见证

## 项目结构

```
├── Dockerfile              # 多阶段：前端构建 + Python 运行时
├── docker-compose.yml      # app（健康检查）+ verify（验收服务），HOST_PORT 可配
├── verify / verify.py      # 可执行验收入口与验收套件
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI 路由、静态托管
│   │   ├── solver.py       # 精确 MILP 求解、规范化、唯一性与见证
│   │   └── validation.py   # 逐项输入校验
│   ├── tests/              # 求解器（含暴力枚举对照）与 API 测试
│   └── requirements*.txt
└── frontend/               # React + Vite 单页应用
```
