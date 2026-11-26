# Prova — hướng dẫn cho Claude Code

**Prova** là security coding agent tự xây bằng Anthropic SDK thuần, có benchmark riêng. Nguyên tắc: *"No proof, no finding."*
Mọi lỗ hổng báo ra phải có PoC test chạy được trong sandbox. Đây là dự án học sâu + đưa vào CV của chủ repo.

## Đọc gì khi bắt đầu session

1. [PLAN.md](PLAN.md): plan tổng (mạch truyện, bản đồ Act, mục lục milestone + phụ thuộc, kiến trúc, tiến độ).
2. File Act đang làm trong [plans/](plans/) (mỗi milestone: 📖 story, 🎯 mục tiêu, 📐 thiết kế, 🪜 bước, 🧪 kiểm chứng,
   ⚠️ bẫy, 🩹 sửa lỗi sách, 🔬 đào sâu, 📚 tham khảo, ✅ xong khi, 📝 câu hỏi LEARNINGS).
3. Từ M0 trở đi: `ROADMAP.md` (Implemented / In Progress / Backlog P0–P2 / Decisions Log) và `LEARNINGS.md`.
   Mở đầu session: *"Đọc ROADMAP.md, cho biết việc đang dở và P0 tiếp theo."*

## Cách làm việc (bắt buộc)

- **Claude viết code, người dùng đọc và tự chạy.** Mỗi lần **một phần nhỏ** (một file hoặc một hàm): viết code →
  giải thích từng đoạn (vì sao viết vậy, nối với quy ước / anti-pattern nào) → đưa lệnh chạy + kết quả mong đợi →
  **DỪNG**, chờ người dùng gửi kết quả hoặc nói "next".
- **Không tự chạy** script, test, `uv add`, lệnh cài đặt của dự án; đưa lệnh để người dùng chạy. Đọc file, sửa plan thì được.
- Có test thì viết test trước, cho người dùng chạy thấy fail, rồi mới viết code.
- Phần nào người dùng muốn tự viết thì họ sẽ nói; khi đó chỉ gợi ý khung và review.
- Giải thích bằng **tiếng Việt**, từng bước một; đợi người dùng phản hồi trước khi sang phần tiếp.
- Review mọi milestone bằng [plans/shared/anti-patterns.md](plans/shared/anti-patterns.md) và
  [plans/shared/code-conventions.md](plans/shared/code-conventions.md).
- Từ Act III: mọi tuyên bố "tốt hơn" cần ≥ 3 seed, trung bình ± SEM.
- **Sửa chi tiết milestone trong file Act**, không nhồi vào PLAN.md; PLAN.md chỉ giữ mục lục, kiến trúc, tiến độ.
  Giữ nguyên cấu trúc storyline + các mục từng bước khi sửa plan.
- Commit / push chỉ khi người dùng yêu cầu.
- **Phạm vi bảo mật: chỉ phòng thủ**, trên code của người dùng hoặc code được phép. PoC chạy trong sandbox không mạng, nhắm
  vào hàm trong repo. Pentest hệ thống thật là "Project 2", chỉ trong lab được phép.

## Môi trường

- Chỉ có **Anthropic API key**. Đặt trong `.env` của repo này (đã ignore), không bao giờ in hay commit key.
  Model mặc định lấy từ config (hiện `claude-sonnet-5-5`), không ghi cứng.
- Môi trường bằng **`uv`**: build đến đâu `uv add` đến đó (không cài trước, không `requirements.txt` viết tay);
  commit `pyproject.toml` + `uv.lock`; chạy qua `uv run …`. Chi tiết: code-conventions mục 9.
- `.env` đã tạo sẵn với `ANTHROPIC_API_KEY` (dùng chung key với `~/atlas-agents`).
- Python 3.12+, Docker (sandbox, từ M7). Không dùng framework agent cho phần lõi (vòng lặp, tool, harness).
  Ngoại lệ: **LlamaIndex là lõi của tầng RAG ở Act V** (chunking, index, truy hồi trong `kb/`), sau một ranh giới
  `retrieve() -> list[Citation]`; quyết định 2026-11-26.

## Tài liệu nằm ngoài repo

- `~/atlas-agents/`: code sách *Building AI Agents*. Các tham chiếu `chNN`, `shared/…py` trong plan chỉ tới đó.
  Nhiều task benchmark là bug thật tìm thấy trong repo này (xem [plans/shared/bug-sources.md](plans/shared/bug-sources.md)).
- `~/atlas-agents/OtherConcepts/`: sách Gulli & Nawalgaria (PDF), ghi chú theo chương `ghi-chu-sach-gulli.md`,
  tài liệu pytest, link CWE-918.

## Trạng thái hiện tại (cập nhật 2026-11-26)

- **Act I và II xong** (M0a–M9b). **Act III:** M10 (benchmark v0, 8 task trong `bench/tasks/dev/`) và M12
  (record/replay, trajectory viewer) xong; M11 hoãn vào backlog; M12b chưa làm. **Act IV:** M13 phần lõi xong
  (fingerprint, feedback, `run_fix_loop`, `LoopAgent`, `bench.run --loop`); đo vòng lặp chờ Act V vì mọi task đến
  giờ đều được giải ngay lần đầu (Sonnet và Haiku).
- M13 và M13b đã đóng (2026-11-26): `--repro` có tỉ lệ tái hiện 9/9 với Haiku. Bước tiếp theo: **M14** (reviewer tự
  kiểm hay đối kháng), rồi M14b → Act V. Tiến độ theo Act: bảng "Tiến độ" trong `PLAN.md`; chi tiết việc đang dở: `ROADMAP.md`.
- 1342 test đơn vị, 40 test Docker (`uv run pytest -m docker`). Chạy benchmark thật chỉ khi người dùng đồng ý và đã
  nêu ước tính chi phí (`bench.run` không có `--confirm` thì chỉ hiện plan và mức trần).
- Code, comment, docstring và thông báo lỗi viết bằng **tiếng Anh**; giải thích cho người dùng bằng tiếng Việt.

## Quyết định đã chốt (đừng mở lại nếu người dùng không hỏi)

- Tự xây từ số 0 thay vì dùng framework: mục tiêu là hiểu từng lớp (I–II hiểu, III–VII dùng, VIII–IX chia sẻ).
  Riêng tầng RAG của Act V dùng LlamaIndex (đổi 2026-11-26, theo yêu cầu của người dùng).
- Benchmark tự dựng từ bug thật, kiểu SWE-bench; đã xem `openai/mle-bench` và **không** dùng làm benchmark chính (quá tốn,
  leaderboard đóng), chỉ học cách thiết kế (seed ± SEM, chống gian lận, ghi chú contamination).
- Định vị **security coding agent** (Act V là lõi; Điểm dừng 1 = cuối Act V với vuln-bench so Bandit / Semgrep / Claude Code).
- Kho tri thức từ **bản tải chính thức / API** (CWE, CAPEC, OWASP, OSV, GHSA, NVD, KEV, EPSS), **không crawl**; sync theo lịch,
  staging → cổng eval → active; watch mode theo SBOM.
- Công cụ chuẩn (Bandit, Semgrep, Trivy, osv-scanner, gitleaks…) là tín hiệu + baseline, chạy trong sandbox, ghim digest.
- Tên: **Prova** (trước là "Atlas Jr."; đổi 2026-11-21).
