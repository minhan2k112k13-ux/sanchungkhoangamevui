# BÁO CÁO KIỂM TRA — SÀN CHỨNG KHOÁN Web Edition 2.1

**Ngày kiểm tra:** 10/11/2026  
**Nguồn chuyển đổi:** `sanchungkhoanbth(6).docx`  
**Mục tiêu:** tạo dự án web mới tách biệt để triển khai trên Render + Neon; không thay thế website hoặc database cũ.

## 1. Kết quả tổng quan

| Hạng mục | Kết quả |
|---|---|
| Biên dịch Python (`main.py`, `app/*.py`) | Đạt |
| Kiểm tra cú pháp JavaScript (`node --check`) | Đạt |
| Kiểm thử tự động pytest | 6/6 test đạt ở lượt chạy cuối |
| Kiểm tra các ID HTML được JavaScript tham chiếu | Không có ID thiếu hoặc trùng trong phép rà soát tĩnh |
| Kiểm tra các route tĩnh `/`, CSS, JS bằng FastAPI TestClient | Đạt |
| Luồng đăng ký tự đăng nhập, đăng nhập lại | Đạt |
| Thị trường 10 mã, mua BTC, lưu giao dịch | Đạt |
| OWNER tự tạo từ Render Environment; đăng nhập Owner; duyệt ADMIN, chỉnh tiền, quản lý vai trò | Đạt |
| Luồng SPADMIN xin cấp RUBY chờ OWNER duyệt | Đạt |
| Chặn phạt trực tiếp RUBY từ ADMIN và chuyển yêu cầu lên SPADMIN | Đạt |
| Lưu dữ liệu SQLite sau khi khởi tạo lại app trong test | Đạt |
| Kiểm thử kết nối Neon thực tế | Chưa thực hiện — chưa có connection string Neon của người dùng |
| Deploy Render thực tế | Chưa thực hiện — không có quyền truy cập tài khoản Render/GitHub của người dùng |
| Kiểm thử hiển thị trực quan bằng trình duyệt | Chưa hoàn tất — môi trường trình duyệt chặn kết nối localhost với `ERR_BLOCKED_BY_ADMINISTRATOR`; API và file giao diện đã kiểm qua TestClient và rà soát tĩnh |

## 2. Các luồng đã đưa vào test

1. Thành viên đăng ký; API trả tài khoản và CSRF mới, session đã đăng nhập ngay; lấy đủ 10 mã; mua BTC bằng USD; kiểm tra số dư, danh mục, lịch sử giao dịch; đăng xuất rồi đăng nhập lại.
2. OWNER tự được tạo từ `OWNER_USERNAME` + `OWNER_PASSWORD`; đăng nhập bằng form thông thường; API bootstrap cũ không còn tồn tại. OWNER duyệt ADMIN, cộng USD, tặng/thu hồi RUBY, thay đổi vai trò, đọc cấu hình hạn mức, audit log và backup.
3. OWNER xóa được tài khoản không phải OWNER; tài khoản thành viên không truy cập API giới hạn Owner.
4. SPADMIN tạo yêu cầu tặng RUBY và trạng thái phải chờ OWNER; OWNER duyệt; người nhận nhận RUBY; ADMIN không phạt trực tiếp RUBY mà tạo yêu cầu; SPADMIN xử lý luồng phạt/miễn phạt.
5. Tài nguyên web và dữ liệu SQLite còn tồn tại qua lần khởi tạo lại ứng dụng; mật khẩu OWNER cũ không bị ghi đè khi thay đổi biến môi trường sau khi đã tạo.
6. Trang đăng nhập chỉ có hai tab Đăng ký/Đăng nhập; không còn placeholder, form OWNER, hay nhãn phụ cho đơn vị USD.

## 3. Cấu trúc triển khai

- `main.py`: FastAPI backend và API.
- `app/models.py`: mô hình dữ liệu SQLAlchemy.
- `app/security.py`: xử lý mật khẩu.
- `app/market.py`: dữ liệu và mô phỏng thị trường.
- `app/static/`: HTML, CSS, JavaScript giao diện.
- `tests/`: test tự động.
- `render.yaml`: cấu hình dịch vụ Render mới.
- `.env.example`: mẫu biến môi trường, không chứa secret thật.

Ở local, app dùng SQLite. Khi triển khai, đặt `DATABASE_URL` là connection string của **Neon project mới**. App chuẩn hóa URL PostgreSQL, tạo schema ứng dụng trong database được cấu hình.

## 4. Những điểm cần người dùng tự hoàn tất trước khi mở công khai

- Tạo Neon project mới và điền `DATABASE_URL` trong Render; chưa có Neon credentials nên không thể xác nhận kết nối thật.
- Tạo Render Web Service mới từ repository chứa ZIP; không thể tạo service hoặc deploy thay người dùng từ phiên làm việc này.
- Đặt `OWNER_USERNAME`, `OWNER_PASSWORD` và `SESSION_SECRET` trong Render Environment. OWNER được tạo tự động từ môi trường khi database mới khởi động; giao diện chỉ có Đăng ký/Đăng nhập.
- Sau deploy, thử lại `/healthz`, đăng nhập OWNER, đăng ký 1 tài khoản test, giao dịch mô phỏng, vai trò, chat và restart service trước khi mời người khác dùng.

## 5. Giới hạn còn tồn tại

Đây là bản chuyển đổi web có các chức năng cốt lõi được triển khai và test, **không phải port 100%** của 446 trang mã Pygame. Bản web hiện chưa có: nhập/khôi phục backup, toàn bộ dự báo đường giá tương lai theo lịch của bản cũ, mọi cửa sổ phụ và âm báo desktop, Owner-support/impersonation, lịch thưởng ngẫu nhiên theo giờ, và đầy đủ giao diện quản lý/hoàn nợ. Biểu đồ web hỗ trợ MA5/10/20/50/200 nhưng API dự báo chỉ là tín hiệu xu hướng MA đơn giản.

Thị trường được cập nhật khi có request. Nếu Render Free ngủ do không có request thì không thể cam kết giá vẫn tick liên tục trong thời gian service ngủ. Dữ liệu bền vững được thiết kế để lưu trong Neon, không dựa vào ổ đĩa Render ephemeral.

**Kết luận:** bộ mã và các luồng lõi đã vượt qua kiểm thử tự động cục bộ; ZIP có thể dùng làm nền cho một dự án Render + Neon mới. Chưa thể coi là production-verified cho đến khi kết nối với Neon thật, deploy Render thật, và hoàn tất kiểm thử trực quan sau deploy.
