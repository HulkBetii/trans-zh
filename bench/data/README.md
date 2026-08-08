# Dữ liệu benchmark

Thư mục này bị `.gitignore` bỏ qua (trừ chính file README này) — media lớn không
commit vào repo.

Cần hai file:

| File | Là gì |
|---|---|
| `clip.mp4` | Video/audio tiếng Trung, 8–15 phút là đủ |
| `ref.srt` | Phụ đề tham chiếu, **căn tay theo sóng âm** |

## Cách tạo `ref.srt`

```bash
uv run zhsub asr bench/data/clip.mp4 --out bench/data/raw.srt
```

Mở `raw.srt` cùng video trong [Subtitle Edit](https://www.nikse.dk/subtitleedit):

1. Sửa lỗi nhận dạng chữ.
2. **Kéo lại mốc bắt đầu theo sóng âm.** Đừng tin mốc do ASR sinh ra.

Lưu thành `ref.srt` (UTF-8).

## Vì sao bước 2 không bỏ qua được

Nếu chỉ sửa chữ mà giữ nguyên timing, `ref.srt` thừa hưởng đúng sai số onset của
paraformer. Benchmark khi đó sẽ báo paraformer gần như hoàn hảo và whisper tệ,
bất kể sự thật thế nào — vì reference *chính là* output của paraformer.

Chỉ sóng âm mới khử được bias đó. Metric onset đo ở cấp ký tự nên phần bias do
*cách ngắt câu* đã được loại sẵn, nhưng bias *timing* thì không có cách nào khác.
