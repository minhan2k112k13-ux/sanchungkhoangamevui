# SÀN CHỨNG KHOÁN — Web Edition 2.1 (Render + Neon)

Đây là **dự án mới độc lập**, chuyển game desktop Pygame trong bản nguồn thành website FastAPI + PostgreSQL. Không dùng lại file dữ liệu JSON cục bộ và không tự động ghi đè website/Neon cũ.

> Toàn bộ USD, tài sản, giá thị trường và giao dịch trong website là **mô phỏng**. Không kết nối ngân hàng, không nhận tiền thật và không phải sản phẩm đầu tư.

## 1. Nội dung

- Đăng ký tài khoản và **đăng nhập ngay trong cùng thao tác**; đăng nhập lại bằng tài khoản đã tạo.
- Một OWNER duy nhất (`baominhadmin`) được tạo tự động từ `OWNER_PASSWORD` trong Render Environment khi database mới khởi động lần đầu. Giao diện chỉ có Đăng ký và Đăng nhập.
- Vai trò: THÀNH VIÊN → TỌA ĐỘ → ADMIN → SPADMIN; OWNER duyệt yêu cầu ADMIN và cấp/thu hồi vai trò. OWNER có thể xóa tài khoản khác.
- Hạn mức mặc định: SPADMIN 5, ADMIN 10, TỌA ĐỘ 20; OWNER chỉnh từ trang quản trị.
- 10 mã tài sản, giá dùng chung, chart có MA5/10/20/50/200, lịch sử tối đa 200 điểm, danh mục, mua/bán bằng USD.
- Thao tác cộng/trừ USD của QTV, ví dụ chuyển tiền, cho nợ; giao dịch được lưu vào PostgreSQL.
- Thành viên ĐỒNG/BẠC/VÀNG/BẠCH KIM/KIM CƯƠNG, thời hạn 3 ngày/1 tháng/1 năm; RUBY/LỤC BẢO là gói đặc biệt không bán.
- Tặng RUBY: OWNER cấp trực tiếp; SPADMIN gửi yêu cầu OWNER duyệt. LỤC BẢO chỉ OWNER tặng; quà tặng có thời hạn 1 ngày.
- Thưởng ngày, miễn phạt RUBY 3 lượt, bảo vệ 80% khoản lỗ đủ điều kiện của LỤC BẢO, chat TỔNG/THÀNH VIÊN/QTV, hòm thư, tố cáo và nhật ký.
- Mật khẩu lưu dạng PBKDF2-HMAC-SHA256 salted hash; session có CSRF token; quyền được kiểm tra ở API, không dựa vào ẩn nút ở trình duyệt.

## 2. Chạy thử trên máy

Cần Python 3.11+.

```bash
python -m venv .venv
# Windows CMD:
.venv\\Scripts\\activate
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt
```

Tạo file `.env` từ `.env.example` và điền `OWNER_PASSWORD`, `SESSION_SECRET` mạnh. Có thể tạo hai giá trị bằng `python scripts/generate_secrets.py`. Giữ `APP_ENV=development` khi chạy qua HTTP trên máy. Để chạy local, không thêm `DATABASE_URL`; app dùng SQLite file `sanchungkhoan_local.db` trong thư mục dự án. File `.env` được tự động nạp và không được đưa lên Git. Chạy:

```bash
uvicorn main:app --reload
```

Mở `http://127.0.0.1:8000`. OWNER được tạo tự động từ biến môi trường khi database mới khởi tạo; sau đó đăng nhập bằng `OWNER_USERNAME` và `OWNER_PASSWORD`. API docs local nằm tại `/api/docs`.

## 3. Tạo Neon database mới

1. Tạo **project Neon mới**, tên gợi ý `sanchungkhoan-web-v2-clean`. Đừng dán connection string của database cũ.
2. Tạo/copy connection string PostgreSQL (nên dùng pooled connection string cho web service).
3. Đưa connection string vào Render dưới tên `DATABASE_URL`. Không gửi connection string cho người khác và không ghi vào file public.

## 4. Tạo Render Web Service mới

1. Tạo một repository Git mới, upload toàn bộ nội dung ZIP này, rồi push lên GitHub.
2. Trên Render, chọn **New → Web Service**, kết nối repository mới.
3. Cấu hình:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Health check path: `/healthz`
4. Tạo biến môi trường:
   - `DATABASE_URL`: connection string của **Neon project mới**.
   - `SESSION_SECRET`: chuỗi bí mật ngẫu nhiên dài.
   - `OWNER_USERNAME`: `baominhadmin`.
   - `OWNER_PASSWORD`: mật khẩu riêng do em tự đặt trong Render Environment, ít nhất 12 ký tự và có cả chữ lẫn số.
   - `OWNER_DISPLAY_NAME`: tên hiển thị (không bắt buộc).
   - `APP_ENV`: `production`.
5. Deploy. Khi database mới khởi động, server tự tạo đúng một OWNER từ các biến môi trường. Không có tab hoặc form thiết lập OWNER trên website; giao diện chỉ hiện Đăng ký và Đăng nhập. Sau khi OWNER đã được tạo thành công, có thể xóa `OWNER_PASSWORD` khỏi Render Environment; server không thay đổi mật khẩu OWNER đã tồn tại khi restart.

Render có thể ngừng service miễn phí sau thời gian không có truy cập; lần mở đầu tiên có thể phải đợi dịch vụ khởi động lại. Dữ liệu quan trọng được lưu trên Neon, không dựa vào ổ đĩa ephemeral của Render.

## 5. Kiểm tra sau deploy

- Mở `/healthz` và kiểm tra DB cùng `owner_configured: true`; đăng nhập OWNER bằng `OWNER_USERNAME` và mật khẩu đã cấu hình trong Render, kiểm tra role label `OWNER` và số dư khởi tạo `500,000 USD`.
- Tạo một tài khoản thành viên mới; xác nhận trang sàn mở ngay, không yêu cầu đăng nhập lại.
- Đăng xuất, đăng nhập lại; thử mua/bán tài sản; làm mới trang; kiểm tra số dư và danh mục còn đúng.
- Thử phân quyền ở ít nhất 2 tài khoản: OWNER duyệt ADMIN, trao SPADMIN; thử quyền cấm của tài khoản thường.
- Thử mua gói (dùng USD), nhận thưởng, chat và gửi tố cáo.
- Restart/deploy lại service; đăng nhập và kiểm tra tài khoản/market vẫn còn trong Neon.

## 6. Những giới hạn đã biết của bản chuyển đổi

Đây là bản port web đã làm mới, không phải bản sao pixel-perfect toàn bộ desktop app. Bản này có **tải backup JSON để xuất thông tin tài khoản** (không gồm hash mật khẩu, lịch sử giao dịch hay toàn bộ chat) nhưng chưa có nút nhập/restore backup; chưa mang toàn bộ cửa sổ phụ Pygame, owner-support/impersonation, mọi nhánh sự kiện đặc biệt và các âm báo desktop sang web. Mã cũ có một số chức năng GUI và dữ liệu JSON phụ thuộc máy; các tính năng đó cần được port/test riêng trước khi gọi là tương đương 100%.

Biểu đồ web có MA5/10/20/50/200; tín hiệu dự báo web hiện chỉ dùng xu hướng MA5/MA20, chưa phải bộ dự báo đường giá tương lai theo lịch đầy đủ ở bản Pygame. Thị trường mô phỏng được cập nhật khi có request và lưu state chung trên database; Render Free có thể ngủ khi không có request, nên không hứa thị trường tick liên tục khi service đang ngủ. Khi service thức dậy, hệ thống chỉ catch-up giới hạn số bước để tránh gây quá tải.

## 7. Đối chiếu và báo cáo

- `FEATURE_MATRIX.md`: bảng chức năng được chuyển từ bản desktop sang web và phần chưa port.
- `BAO_CAO_KIEM_THU.md`: kết quả test, phạm vi đã kiểm, và các bước còn cần chạy sau khi kết nối Neon/Render thật.

## 8. Lệnh kiểm tra

```bash
python -m py_compile main.py app/*.py
pytest -q
```

Không đưa `.env`, database local, mật khẩu hay secret vào repository.
