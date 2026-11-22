# Checklist anti-pattern

> Rút ra từ repo `atlas-agents`, sách *Building AI Agents* và tài liệu testing. Dùng khi review **mọi** milestone.
> Mỗi mục có: ví dụ thật, cách phát hiện khi review, cách làm đúng.

[← Về plan tổng](../../PLAN.md)

| # | Anti-pattern | Ví dụ thật | Phát hiện khi review | Làm đúng |
|---|---|---|---|---|
| 1 | **Fail-open**: lỗi thì cho qua | guardrail L2 (ch14), reviewer capstone, tester `ImportError → passed` (ch22), A2A fallback `approved` | tìm `except` trả về giá trị "an toàn cho người dùng" như `passed=True`, `approved`, `blocked=False` | lỗi ở lớp bảo vệ thì **chặn**; ghi rõ lý do |
| 2 | **Xin JSON bằng prompt** rồi `json.loads` / regex | `\{[^{}]*\}` ở capstone, guardrail L2, scaffold_optimizer | tìm `json.loads(response.content[0].text)` và regex trên output LLM | **structured outputs** (`messages.parse` / `output_config.format`, hoặc tool `strict: true`), rồi Pydantic validate, fail-closed (M3). Ép `tool_choice` như sách bị 400 trên model mới |
| 3 | **Cắt bớt không báo** | `[:8000]` (ch13), `MAX_PAGES` (ch13), `--limit 50` (ch22) | tìm slicing `[:N]` trên dữ liệu đưa cho model hoặc người dùng | cắt thì ghi rõ "đã cắt X/Y" vào output |
| 4 | **Docstring hứa nhiều hơn code** | capstone "MCP, checkpointer", scaffold_optimizer "validates" | đọc docstring rồi tìm đoạn code thực hiện từng lời hứa | docstring chỉ mô tả điều code làm; việc chưa làm thì ghi TODO |
| 5 | **So sánh một lần chạy** | prompt_critic, CI gate, "~25% savings" | tìm kết luận "tốt hơn" không có seed hay SEM | ≥ 3 seed, trung bình ± SEM, so sánh theo cặp |
| 6 | **`except Exception` nuốt lỗi** | khắp repo | tìm `except Exception` chỉ `print` hoặc `pass` | bắt đúng loại lỗi; lỗi không lường trước thì để nổi lên hoặc log kèm traceback |
| 7 | **Đọc env / tạo client lúc import**, model ghi cứng | `client = anthropic.Anthropic()` đầu file; `"claude-sonnet-4-6"` ghi cứng | tìm lời gọi tạo client hoặc đọc `os.environ[...]` ở cấp module | factory nhận config; model lấy từ config |
| 8 | **Không dọn tài nguyên trong `finally`** | sandbox ch12, phiên Gemini Live ch13 | tìm `create/start/Sandbox()` không có `finally` hay context manager | context manager; test riêng cho đường lỗi |
| 9 | **Feedback sai nguồn hoặc không truyền vào** | capstone retry không nhận lỗi; `fix_loop.py` ghi bài học từ output *trước* khi vá | lần theo dữ liệu từ verifier đến lượt gọi model kế tiếp | bài học = bản vá vừa rồi + lỗi *hiện tại* |
| 10 | **Thất bại trông như thành công** | `✅ PR created:` với URL rỗng; nightly sweep "0 issues" khi token hết hạn | tìm `print("✅ …")` không kiểm kết quả lệnh | kiểm exit code / response; lỗi thì báo lỗi và cảnh báo |
| 11 | **Mock tại nơi định nghĩa thay vì nơi dùng** | patch `random.randint` trong khi code `from random import randint`; thay cả `sys.modules` | tìm `patch("thu_vien.ham")` cho hàm đã được import trực tiếp | `monkeypatch.setattr("module_dung.ham", fake)` |
| 12 | **Dữ liệu không tin cậy trong system prompt** | planner capstone trong sách: nội dung issue vào `system` qua `.format()` | tìm chỗ ghép issue, file, web, output tool vào `system=` | dữ liệu ngoài ở lượt user, bọc thẻ `<untrusted_…>`; render bằng thay chuỗi |
| 13 | **Allowlist lệnh nhưng chạy `shell=True`** | ch15 `harness.py`: kiểm `split()[0]`, nên `pytest && rm -rf ~` lọt | tìm `shell=True` và allowlist dựa trên chuỗi | argv dạng list, allowlist xét cả tham số, interpreter chỉ chạy trong sandbox |
| 14 | **Dùng tool / action theo tag thay vì digest** | sự cố `trivy-action` 3/2026: 76/77 tag bị đẩy đè sang mã đánh cắp credential | tìm `uses: …@v`, `image: …:latest`, `pip install` không khóa hash | ghim full SHA / `@sha256:`, lock file có hash, chạy không credential |

## Câu hỏi nhanh trước khi commit

1. Nếu lớp này lỗi, hệ thống **chặn** hay **cho qua**?
2. Output của model có được parse bằng regex hay `json.loads` ở đâu không?
3. Có con số nào mình tuyên bố mà chưa chạy ≥ 3 seed?
4. Có dữ liệu ngoài nào đi vào `system` prompt?
5. Có lệnh nào chạy qua shell?
6. Có tài nguyên nào (sandbox, process, phiên) không có đường dọn khi lỗi?
7. Có tool, image hay action nào được dùng theo tag thay vì digest?
