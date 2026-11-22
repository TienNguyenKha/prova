# Quy ước code

> Áp dụng cho **mọi** milestone. Mục tiêu: code đọc giống nhau từ Act I đến Act IX, test được mà không cần `patch`,
> và tự động chặn được phần lớn [anti-pattern](anti-patterns.md) bằng tool thay vì bằng trí nhớ.
> Dựng nền móng ở [M0a](../act-1-api.md#m0a--nền-móng-repo). Sửa file này khi đổi quy ước, và ghi lý do vào Decisions Log của `ROADMAP.md`.

[← Về plan tổng](../../PLAN.md)

## Học từ `atlas-agents/shared/`

| Ở `atlas-agents` | Ở `prova` | Giữ gì, bỏ gì |
|---|---|---|
| `shared/config.py` | `prova/config.py` | **Giữ:** một chỗ duy nhất đọc env; đọc `.env` theo đường dẫn project, không theo `cwd`; lỗi thiếu key có hướng dẫn sửa. **Bỏ:** đọc env và tạo biến global lúc import; `load_dotenv` sửa `os.environ` của cả process; bốn provider. |
| `shared/skills.py` `Skill(ABC)` | `prova/tools/base.py` `Tool(Protocol)` | **Giữ:** tách interface khỏi registry. **Bỏ:** ABC bắt kế thừa; schema kiểu OpenAI viết tay; `args: dict` chưa validate; trả `Any`. |
| `shared/skills.py` `SkillRegistry` | `prova/registry.py` `Registry[T]` + `ToolRegistry` | **Bỏ:** `register` ghi đè không báo khi trùng tên (chính là lỗ "tool shadowing" ở Act VII); lỗi trả về dạng chuỗi `"Error: …"`, không có `is_error`. |
| `shared/__init__.py` | `prova/__init__.py` | chỉ có `__version__`, không có side effect |

Không có thư mục `shared/` riêng: chính package `prova/` là phần dùng chung; `experiments/` là nơi chạy thử.

## 1. Cấu hình

- **Chỉ `load_settings()` được đọc env**, và chỉ gọi **một lần** ở đầu chương trình (CLI, script thí nghiệm, test fixture).
  Mọi tầng khác nhận `Settings` qua tham số. `import prova` khi không có `.env` **không được lỗi**.
- `Settings` là Pydantic model **bất biến** (`frozen=True`). Key dùng `SecretStr`, nên `repr` và log không lộ key.
- Biến env dùng tiền tố `PROVA_` (`PROVA_MODEL`, `PROVA_CHEAP_MODEL`, `PROVA_MAX_TOKENS`…). Riêng `ANTHROPIC_API_KEY` giữ
  tên chuẩn của SDK.
- Thứ tự ưu tiên: **tham số truyền vào > biến môi trường > file `.env` > mặc định**. Đọc `.env` bằng `dotenv_values()`
  (trả về dict), **không** dùng `load_dotenv()` (sửa `os.environ` toàn cục, test này rò sang test khác).
- Model không ghi cứng ở đâu ngoài giá trị mặc định trong `Settings`.
- Thiếu hoặc sai giá trị thì báo `ConfigError` **ngay lúc load**, nói rõ biến nào và cách sửa; không để lỗi lộ ra ở
  lượt gọi API thứ 20.

```python
# prova/config.py — phác thảo
class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    anthropic_api_key: SecretStr
    model: str = "claude-sonnet-5-5"
    cheap_model: str = "claude-haiku-5-5"
    max_tokens: int = Field(4096, gt=0)
    request_timeout_s: float = Field(120.0, gt=0)
    project_root: Path

def load_settings(env_file: Path | None = None, *, environ: Mapping[str, str] | None = None,
                  **overrides: object) -> Settings:
    """Gộp mặc định < .env < environ < overrides; lỗi thì ConfigError kèm tên biến."""
```

`environ` cho phép test truyền dict giả thay vì sửa `os.environ`.

## 2. Kiểu dữ liệu: chọn cái nào

| Dùng | Cho | Ví dụ |
|---|---|---|
| **Pydantic `BaseModel`** | dữ liệu **đi qua ranh giới**: từ model, từ file, ra file, qua mạng | args của tool, output có cấu trúc, `contract.yaml`, `task.yaml`, sự kiện trajectory, `KbDoc`, `SecurityFinding`, `Alert` |
| **`@dataclass(frozen=True, slots=True)`** | giá trị nội bộ, đã tin cậy | `UsageRecord`, `ToolResult`, `AgentResult` |
| **`StrEnum`** | tập giá trị đóng cần lưu ra file | `ErrorCode`, `Permission` |
| **`Literal[...]`** | tập nhỏ, chỉ dùng trong một chỗ | `status` của `AgentResult` |
| **`Protocol`** | interface | `Tool`, `LLMClient`, `Sandbox`, `Clock`, `KbSource`, `Scanner` |

Quy tắc:
- **Không truyền `dict` trần qua nhiều tầng.** `dict` chỉ xuất hiện đúng ở ranh giới gọi SDK (content block) và được
  chuyển sang kiểu có tên ngay sau đó.
- Pydantic ở ranh giới dùng `extra="forbid"` để trường lạ bị báo lỗi, không bị lờ đi.
- Dữ liệu ghi ra file (trajectory, kết quả benchmark) có trường `schema_version`.
- Ưu tiên **`Protocol` hơn ABC**: test chỉ cần một class giả có đúng method, không phải kế thừa.

## 3. Registry

Dự án có nhiều registry cùng một hình dạng: tool (M4), skill (M18), MCP server (M8b), nguồn KB (S1), scanner (S3/S6),
hook (M8). Viết **một** `Registry[T]` dùng chung, rồi các registry chuyên biệt bọc lấy nó.

### Luật

1. **Đăng ký tường minh** trong composition root (`build_app`), **không** dùng decorator đăng ký lúc import.
   Decorator lúc import tạo trạng thái global, thứ tự phụ thuộc thứ tự import, và test này thấy tool của test khác.
2. **Trùng tên thì báo lỗi** (`DuplicateNameError`), không ghi đè.
3. **Tên hợp lệ** theo regex chung và theo giới hạn tên tool của API (kiểm tra tài liệu hiện tại); tool MCP có
   namespace `mcp__<server>__<tool>`.
4. **Đóng băng** (`freeze()`) sau khi `build_app` xong; đăng ký thêm sau đó là lỗi. Registry được truyền đi như giá trị chỉ đọc.
5. **Thứ tự ổn định** (theo tên): danh sách `tools` gửi lên API giống hệt nhau giữa các lượt và các lần chạy, cần cho
   prompt caching (M23) và để kết quả benchmark tái lập được.
6. Lỗi **do model gây ra** (gọi tool không tồn tại, args sai) là **dữ liệu**: trả `ToolResult(is_error)` cho model sửa.
   Lỗi **do code mình** (đăng ký trùng, gọi `get` tên không có trong code) là **exception**.

```python
# prova/registry.py — phác thảo
class Named(Protocol):
    @property
    def name(self) -> str: ...

class Registry[T: Named]:
    def __init__(self, kind: str) -> None: ...          # kind = "tool", "skill"… dùng trong thông báo lỗi
    def register(self, item: T) -> None: ...            # DuplicateNameError, InvalidNameError, RegistryFrozenError
    def freeze(self) -> None: ...
    def get(self, name: str) -> T: ...                  # UnknownNameError
    def find(self, name: str) -> T | None: ...          # cho chỗ "tên đến từ model"
    def __contains__(self, name: object) -> bool: ...
    def __iter__(self) -> Iterator[T]: ...              # sắp theo tên
    def __len__(self) -> int: ...
```

```python
# prova/tools/registry.py — phác thảo (M4)
class ToolRegistry:
    def __init__(self, tools: Registry[Tool]) -> None: ...
    def to_api_tools(self, allowed: Set[Permission]) -> list[dict]: ...   # schema sinh từ args_model, theo thứ tự tên
    def dispatch(self, name: str, raw_input: object, ctx: RunContext) -> ToolResult:
        # 1. find(name) không có          → ToolResult(error_code=NOT_FOUND, message liệt kê tên gần đúng)
        # 2. args_model.model_validate()  → lỗi thì INVALID_ARGS, chỉ rõ trường nào sai
        # 3. tool.run(args, ctx)
        #    - ToolError(code)            → ToolResult với code đó
        #    - exception khác             → log kèm traceback + metric, ToolResult(INTERNAL);
        #                                   khi ctx.strict (dùng trong test) thì raise lại
        ...
```

## 4. Lỗi

```
ProvaError
├── ConfigError                     # load_settings
├── RegistryError
│   ├── DuplicateNameError  ├── UnknownNameError  ├── InvalidNameError  └── RegistryFrozenError
├── ToolError(code: ErrorCode)      # tool tự báo lỗi có phân loại
├── SandboxError                    # M7
├── LLMError                        # bọc lỗi SDK ở tầng llm/, giữ nguyên exception gốc qua `raise … from e`
└── BudgetExceededError             # M15/M24
```

- **Không dùng `except Exception` trần** (ruff `BLE001` sẽ báo). Ngoại lệ duy nhất: các **ranh giới** đã liệt kê
  (dispatch tool, vòng ReAct, worker), luôn log kèm traceback và đếm metric.
- Chuyển đổi lỗi **đúng ba chỗ**: trong thư viện thì `raise`; ở ranh giới tool thì thành `ToolResult`; ở CLI thì thành
  thông báo + exit code khác 0.
- Lỗi ở lớp bảo vệ thì **chặn**, không cho qua (anti-pattern #1).

## 5. Phụ thuộc và composition root

- Mỗi tầng nhận thứ nó cần **qua tham số**: `client`, `settings`, `clock`, `rng`, `workspace`, `sandbox`.
  Không import client hay settings từ module khác.
- `Clock` và seed ngẫu nhiên cũng được truyền vào, để test và benchmark tái lập được.
- Mọi thứ được lắp ở **một** chỗ:

```python
# prova/app.py — phác thảo, lớn dần qua từng Act
@dataclass(frozen=True)
class App:
    settings: Settings
    llm: LLMClient
    tools: ToolRegistry                 # M4
    # sandbox (M7), hooks (M8), mcp (M8b), kb (S1), scanners (S3)… thêm dần

def build_app(settings: Settings, *, llm: LLMClient | None = None) -> App:
    """Tạo client, đăng ký mọi thứ, freeze registry. Test truyền llm giả."""
```

- Test **không cần `patch`** cho phụ thuộc của chính mình: truyền đồ giả vào. `monkeypatch` chỉ dành cho thứ bên ngoài
  không truyền vào được (anti-pattern #11).

## 6. Log và output

- `logger = logging.getLogger(__name__)` ở cấp module là được (không có side effect). **Chỉ CLI** cấu hình logging.
- Log có cấu trúc (JSON) khi chạy service; dạng đọc được khi chạy tay. Có **filter che secret** (key, token).
- `print` chỉ ở `experiments/` và CLI (ruff `T201` báo ở chỗ khác).
- Trajectory (M5) là luồng sự kiện riêng, không trộn với log.

## 7. Đặt tên và bố cục

- Package nằm ở **`src/prova/`** (src layout). Trong các file plan, đường dẫn `prova/x.py` nghĩa là `src/prova/x.py`.
  Lý do: test và script luôn import bản package đã cài, không vô tình import một file cùng tên ở thư mục hiện tại.
- Module là danh từ (`registry.py`, `workspace.py`); tool là `động_từ_danh_từ` (`read_file`, `run_tests`).
- Hằng số `UPPER_SNAKE`; không có biến global thay đổi được.
- Một module, một trách nhiệm; quá ~400 dòng thì tách.
- `experiments/actN/mX_*.py`: script thí nghiệm, được `print`, **không bao giờ** được package `prova/` import.
- Docstring chỉ mô tả điều code làm; việc chưa làm thì ghi `TODO(Mxx)` (anti-pattern #4).

## 8. Test

```
tests/
├── conftest.py          # chặn mạng mặc định; fixture settings giả (environ dict), app giả
├── fakes.py             # FakeLLM (trả chuỗi response định sẵn), FakeClock, FakeSandbox
├── unit/                # cùng cấu trúc với prova/
└── integration/         # cần Docker → marker `docker`
```

- Marker: `live` (gọi API thật), `docker`, `slow`. Mặc định chạy `-m "not live and not docker"`; `--strict-markers`.
- **Unit test không bao giờ gọi mạng**: `conftest.py` chặn socket, test nào lỡ gọi thì fail ngay.
- Mỗi bug tìm thấy: viết test fail trước, rồi mới sửa (quy tắc hồi quy trong PLAN).
- Test đường lỗi ngang hàng với đường thành công (trùng tên, đã freeze, thiếu key, file không tồn tại…).

## 9. Môi trường và công cụ

### Quản lý môi trường bằng `uv`: build đến đâu, cài đến đó

- **Không cài trước** những gì chưa dùng. Milestone nào cần thư viện mới thì `uv add <pkg>` đúng lúc đó; `uv` tự ghi vào
  `pyproject.toml` và cập nhật `uv.lock`.
- **Không có `requirements.txt` viết tay.** Nguồn sự thật là `pyproject.toml` (khai báo) + `uv.lock` (phiên bản chính xác,
  có hash). **Commit cả hai.** Khi cần file requirements (ví dụ cài vào image sandbox ở M7):
  `uv export --format requirements-txt --no-dev > sandbox/requirements.txt` (có hash, đúng tinh thần anti-pattern #14).
- Chạy mọi thứ qua `uv run` (`uv run pytest`, `uv run python experiments/act1/m0_first_call.py`), không cần `activate`.
- Mỗi lần `uv add`: commit chung với code cần nó; thư viện "lớn" (framework, SDK mới) ghi một dòng lý do vào Decisions Log.

| Lệnh | Khi nào |
|---|---|
| `uv init --package --python 3.12` | một lần, M0a |
| `uv add <pkg>` / `uv add --dev <pkg>` | khi milestone cần |
| `uv remove <pkg>` | khi bỏ không dùng |
| `uv sync` | sau khi pull hoặc đổi máy |
| `uv lock --upgrade-package <pkg>` | nâng một thư viện có chủ đích (ví dụ khi watch S6b báo lỗ hổng) |

Lịch thêm dependency **dự kiến** (chỉ để hình dung, thêm khi thật sự tới):

| Milestone | Thêm |
|---|---|
| M0a | `anthropic`, `pydantic`, `python-dotenv`; dev: `pytest`, `ruff`, `pyright` |
| M5 / M9 | `pyyaml` (permissions, contract) |
| M8b | `mcp` |
| M10 | dev: `pytest-cov` |
| S1 | `httpx` (tải nguồn tri thức) |
| S2 | thư viện BM25 + embedding (chọn khi tới, so sánh trước) |
| M24–M25 | `prometheus-client`, web framework cho service |

### `pyproject.toml` (phần cấu hình tool)

```toml
[project]
name = "prova"
requires-python = ">=3.12"
dependencies = []          # uv add sẽ điền dần

[dependency-groups]
dev = []                   # uv add --dev sẽ điền dần

[tool.ruff]
line-length = 110
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RET", "PTH", "S", "BLE", "T20"]
# S = luật của Bandit, BLE = chặn `except Exception` trần, T20 = chặn print
[tool.ruff.lint.per-file-ignores]
"experiments/**" = ["T20"]
"tests/**" = ["S101"]          # assert trong test là bình thường

[tool.pyright]
include = ["src", "tests"]
strict = ["src"]

[tool.pytest.ini_options]
addopts = "-m 'not live and not docker' --strict-markers"
markers = ["live: gọi API thật", "docker: cần Docker", "slow: chạy lâu"]
```

Ba lệnh phải xanh trước mỗi commit: `uv run ruff check .`, `uv run pyright`, `uv run pytest`. Từ M10 thêm CI chạy cùng ba lệnh này.

## Checklist khi review code

- [ ] Có chỗ nào đọc env hoặc tạo client ngoài `load_settings` / `build_app` không?
- [ ] Có `dict` trần đi qua nhiều tầng không?
- [ ] Registry mới có dùng `Registry[T]`, báo lỗi khi trùng, freeze sau khi build không?
- [ ] Lỗi từ model là `ToolResult`, lỗi từ code là exception: có lẫn không?
- [ ] Có `except Exception` ngoài các ranh giới đã liệt kê không?
- [ ] Test có dùng `patch` cho phụ thuộc lẽ ra truyền vào được không?
- [ ] Có gì ghi ra file mà thiếu `schema_version` không?
