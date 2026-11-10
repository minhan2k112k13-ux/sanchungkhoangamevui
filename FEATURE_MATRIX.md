# Bảng đối chiếu chức năng — SÀN CHỨNG KHOÁN Web Edition 2.1

Đối chiếu theo mã Pygame được cung cấp trong `sanchungkhoanbth(6).docx`. Đây là bảng chuyển đổi chức năng, không phải cam kết sao chép 100% mọi hành vi của bản desktop.

| Nhóm | Có trong bản web mới | Trạng thái / ghi chú |
|---|---|---|
| Đăng ký | Tạo tài khoản, mã tài khoản, số dư khởi tạo 500.000 USD mô phỏng | Đăng ký thành công tự tạo session đăng nhập; không cần quay lại form login |
| Đăng nhập | Đăng nhập, đăng xuất, mật khẩu salted PBKDF2-HMAC-SHA256 | Có giới hạn thử sai; test lại sau đăng xuất |
| OWNER | Tạo OWNER đầu tiên với username `baominhadmin`, OWNER duy nhất | Tự khởi tạo một lần từ `OWNER_USERNAME` + `OWNER_PASSWORD` trên Render Environment; không có form OWNER trên website |
| Vai trò | THÀNH VIÊN, TỌA ĐỘ, ADMIN, SPADMIN, OWNER | OWNER có thể duyệt ADMIN, thăng/giáng cấp và cấp/thu hồi SPADMIN; hạn mức mặc định 20/10/5 cho TỌA ĐỘ/ADMIN/SPADMIN |
| Quản trị tài khoản | Danh sách tài khoản, cấm/mở cấm theo quyền, OWNER xóa tài khoản không phải OWNER | Có kiểm tra ở API phía server; không chỉ dựa vào ẩn nút trên giao diện |
| Chỉnh USD | Cộng/trừ USD | OWNER có thể chọn tài khoản; ADMIN/SPADMIN chỉ chỉnh tài khoản đang đăng nhập. Không phải tiền thật |
| Thị trường | 10 mã: GOLD, SILVER, COPPER, PLATINUM, OIL, BRENT, GAS, BTC, ETH, SOL | Giá dùng chung giữa các tài khoản, lưu DB; crypto 24/7, tài sản truyền thống theo lịch mô phỏng |
| Biểu đồ | Canvas chart, lịch sử tối đa 200 điểm, MA5/MA10/MA20/MA50/MA200 | Có biểu đồ và chỉ báo trung bình động; bản web không port nguyên bộ dự báo đường giá tương lai của bản Pygame |
| Mua/bán | Mua/bán theo số USD mô phỏng, cập nhật ví và danh mục | Đã kiểm thử mua BTC; giao dịch được lưu DB |
| Thành viên thường | ĐỒNG, BẠC, VÀNG, BẠCH KIM, KIM CƯƠNG | Có lựa chọn 3 ngày/1 tháng/1 năm và giá/bonus/forecast_seconds theo bảng gốc ở backend |
| RUBY | Gói đặc biệt không bán; OWNER tặng trực tiếp; SPADMIN phải xin OWNER duyệt | Có 3 lượt miễn phạt; OWNER thu hồi được. Kiểm thử có luồng chờ duyệt |
| LỤC BẢO | Gói đặc biệt không bán; OWNER là người quản lý/tặng | Quà tặng 1 ngày; có logic bảo vệ 80% khoản lỗ đủ điều kiện và thưởng ngày |
| Thưởng ngày | Thưởng Premium và thưởng TỌA ĐỘ | Tọa độ có thưởng ngày theo cấu hình game; không phải lợi nhuận thực |
| Chat | CHAT TỔNG, CHAT THÀNH VIÊN, CHAT QTV | Kiểm tra quyền phòng chat phía API; tin nhắn lưu database |
| Tố cáo / kỷ luật | Gửi tố cáo, quyết định của quản trị, xử lý yêu cầu liên quan RUBY | ADMIN/TỌA ĐỘ không tự phạt RUBY; chuyển luồng lên SPADMIN. Đã có test luồng SPADMIN và miễn phạt RUBY |
| Hòm thư | Inbox thông báo và đánh dấu đã đọc | Có API và lưu database |
| Nhật ký | Audit log thao tác quản trị | OWNER xem nhật ký |
| Sao lưu | Xuất backup JSON một chiều | Chưa có giao diện nhập/khôi phục backup ở bản web |

## Các chức năng chưa port đầy đủ

- Toàn bộ giao diện cửa sổ phụ, thao tác chuột/bàn phím, thông báo âm thanh/desktop và bố cục Pygame không được chuyển nguyên trạng sang web.
- Công cụ dự báo giá tương lai theo lịch/đường đi được lên kế hoạch trong bản desktop chưa được port đầy đủ; API dự báo hiện tại là phân tích xu hướng MA đơn giản.
- Chưa có giao diện khôi phục backup; file backup web hiện chỉ xuất một phần dữ liệu cần thiết và cố ý không xuất hash mật khẩu.
- Chưa chuyển chế độ OWNER hỗ trợ/đăng nhập xem tài khoản khác (impersonation), lịch thưởng ngẫu nhiên theo giờ, đầy đủ màn hình quản lý nợ/hoàn nợ, và toàn bộ nhánh sự kiện đặc biệt.
- Những chức năng phụ nêu trên phải được phát triển và kiểm thử riêng trước khi tuyên bố tương đương hoàn toàn với bản desktop.
