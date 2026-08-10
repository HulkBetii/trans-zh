# Baselines

Artifact của những lần chạy đáng giữ để đối chiếu về sau. Đây **không** phải dữ
liệu test — không có test nào đọc thư mục này. Nó tồn tại vì sinh lại tốn tiền API
hoặc tốn hạn mức tin nhắn, mà không có bản đối chiếu thì mọi câu hỏi kiểu "đổi
provider có làm bản dịch tệ đi không" đều chỉ trả lời được bằng cảm tính.

## bilibili-757c328d

Job `美国最会逃的男人越狱界的扛把子三次越狱直接封神` — 35 phút, 452 câu sau khi
S2 ngắt, dịch cả `vi` lẫn `en`.

Sinh bằng **API OpenAI**, cấu hình lúc đó: `gpt-4o-mini` cho S2, `gpt-5` cho S3/S4,
`batch_size = 40`, `review_pass = true`. Tốn khoảng 232.000 token input và 49.000
token output cho 60 lần gọi.

Giữ lại vì đây là mốc so sánh duy nhất còn tồn tại từ thời chạy bằng API. Sau khi
`zhsub.toml` chuyển sang provider `chatgpt_web`, `work/` đã bị ghi đè và không có
cách nào lấy lại bản này mà không trả tiền API lần nữa.

Số đo rút ra từ nó nằm ở mục "ChatGPT web qua Playwright" trong README gốc.

Cách dùng: trỏ script so sánh vào `translations.vi.json` ở đây và bản trong
`work/<job_id>/`. Lưu ý `segments.json` phải giống nhau thì mới so từng câu được —
S2 chạy lại sẽ cho ranh giới câu khác đi.
