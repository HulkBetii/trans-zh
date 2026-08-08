# zhsub

Pipeline dịch phụ đề tiếng Trung sang tiếng Việt / tiếng Anh. ASR chạy **local**
(không upload audio lên cloud), chỉ khâu dịch mới gọi LLM API.

Hai tiêu chí đánh giá, theo thứ tự ưu tiên:

1. **Bản dịch chuẩn** — đúng nghĩa, tự nhiên, thuật ngữ nhất quán xuyên suốt video.
2. **Timeline sát video gốc** — mỗi dòng phụ đề khớp đúng thời điểm câu đó được nói.

Nguyên tắc bất di bất dịch: **timestamp chỉ đến từ ASR, không bao giờ từ LLM.**
LLM chỉ được trả về text và vị trí ngắt trên chuỗi ký tự; mọi mốc thời gian đều
tra ngược về mảng token của S1.

> **Trạng thái: Giai đoạn 0 (benchmark).** Mới có S0 (ingest), S1 (ASR) và
> harness đo lường. S2–S5 thuộc giai đoạn build đầy đủ, chưa làm.

---

## Cài đặt trên Windows

### 1. ffmpeg

```bash
winget install Gyan.FFmpeg
```

Mở lại terminal rồi kiểm tra `ffmpeg -version` và `ffprobe -version`. Cả hai phải
nằm trong `PATH` — pipeline gọi trực tiếp, không đi qua wrapper Python nào.

### 2. uv

```bash
winget install astral-sh.uv
```

### 3. Dependencies (bao gồm PyTorch bản CUDA)

```bash
uv sync --extra bench
```

Chỉ vậy thôi. `pyproject.toml` đã khai báo sẵn index của PyTorch:

```toml
[[tool.uv.index]]
name = "pytorch-cu128"
url = "https://download.pytorch.org/whl/cu128"
explicit = true

[tool.uv.sources]
torch = [{ index = "pytorch-cu128", marker = "sys_platform == 'win32' or sys_platform == 'linux'" }]
```

PyPI mặc định chỉ có torch bản **CPU** trên Windows, nên thiếu khai báo này thì
`uv sync` cài xong vẫn không thấy GPU. cu128 chạy được từ Ampere (RTX 3060,
sm_86) trở lên.

Kiểm tra:

```bash
uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

Muốn ép chạy CPU: đặt `ZHSUB_DEVICE=cpu`, hoặc `--device cpu` trên dòng lệnh.
Không có GPU thì mọi thứ vẫn chạy, chỉ chậm hơn nhiều.

### 4. Model ASR

Lần chạy đầu tiên tự tải `paraformer-zh`, `fsmn-vad`, `ct-punc` từ ModelScope
(~1GB). Server ở Trung Quốc nên từ Việt Nam có thể chậm. Đổi chỗ lưu:

```bash
setx MODELSCOPE_CACHE D:\models\modelscope
```

Mặc định là `%USERPROFILE%\.cache\modelscope`.

### 5. Vài lưu ý riêng của Windows

- **Đường dẫn dài**: model cache của ModelScope lồng khá sâu. Gặp lỗi
  `FileNotFoundError` với đường dẫn dài ngoằng thì bật long path:
  `New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force`
- **Encoding**: phụ đề xuất ra là UTF-8 có BOM để Windows Media Player, PotPlayer
  và Subtitle Edit nhận đúng tiếng Trung/tiếng Việt mà không phải chọn tay.

### Linux

Y hệt, chỉ thay bước ffmpeg bằng `apt install ffmpeg`.

---

## Dùng

### Chỉ chạy ASR, để kiểm tra timeline

```bash
uv run zhsub asr video.mp4 --out raw.srt
```

Nhận file local hoặc URL (Bilibili...). Video cần đăng nhập thì đặt
`ingest.cookies_from_browser` trong `zhsub.toml` — yt-dlp đọc cookie từ browser
bạn đang đăng nhập sẵn; pipeline không bao giờ tự nhập tài khoản hay mật khẩu.

Kết quả trung gian nằm ở `work/<job_id>/`. `job_id` sinh tất định theo nguồn nên
chạy lại cùng một file là dùng lại kết quả cũ thay vì làm lại từ đầu.

---

## Benchmark (Giai đoạn 0)

So `paraformer-zh` với `faster-whisper large-v3` trên chính video của bạn.

### Bước 1 — sinh bản nháp

```bash
uv run zhsub asr bench/data/clip.mp4 --out bench/data/raw.srt
```

Dùng clip 8–15 phút là đủ.

### Bước 2 — căn tay reference (**bước này không bỏ qua được**)

Mở `raw.srt` cùng video trong [Subtitle Edit](https://www.nikse.dk/subtitleedit),
rồi:

1. Sửa lỗi nhận dạng chữ.
2. **Kéo lại mốc bắt đầu theo sóng âm**, đừng giữ mốc do ASR sinh ra.

Lưu thành `bench/data/ref.srt`.

Bước 2 là bắt buộc. Chỉ sửa chữ mà giữ nguyên timing thì reference thừa hưởng
đúng sai số onset của paraformer, và benchmark sẽ tự khen paraformer.

### Bước 3 — đo

```bash
uv run python -m bench.run --media bench/data/clip.mp4 --ref bench/data/ref.srt
```

In ra bảng markdown.

### Các metric đo cái gì

**CER** — chuẩn hoá **giống hệt nhau ở cả hai phía** trước khi đo: phồn thể →
giản thể (whisper large-v3 hay trả phồn thể), bỏ dấu câu (`ct-punc` chèn, ref
viết tay thì tuỳ), fullwidth → halfwidth, chữ số Ả Rập → chữ Hán. Thiếu mấy bước
này thì con số đo được là nhiễu chứ không phải chất lượng nhận dạng.

**Sai số onset** — đo ở **cấp ký tự**, không so mốc bắt đầu của segment.
paraformer ngắt câu theo khoảng lặng VAD, faster-whisper ngắt theo cửa sổ 30
giây; so `start` giữa hai engine là đang đo *chính sách ngắt câu* chứ không đo
*độ chuẩn timestamp*. Cách làm: align chuỗi ký tự ref với chuỗi ký tự dự đoán rồi
lấy thời điểm của ký tự đầu tiên của mỗi cue ref.

**Coverage** — tỉ lệ cue ref có ký tự đầu khớp được. Coverage thấp thì median và
p90 kém đại diện, phải đọc kèm.

**RTF** — thời gian xử lý chia độ dài audio, không tính thời gian nạp model.

faster-whisper bắt buộc chạy với `word_timestamps=True`; timestamp cấp segment
mặc định của nó quá thô để so sánh công bằng.

---

## Kiến trúc

Mỗi stage đọc/ghi file JSON trong `work/<job_id>/` và chạy lại độc lập được:

| Stage | Việc | Output |
|---|---|---|
| S0 `ingest` | chuẩn hoá về WAV 16kHz mono | `ingest.json`, `audio.wav` |
| S1 `vad_asr` | FunASR → transcript + timestamp cấp token | `asr.json` |
| S2 `segment` | ngắt câu lại theo ngữ nghĩa (LLM), giữ nguyên timestamp gốc | `segments.json` |
| S3 `glossary` | trích thuật ngữ/tên riêng toàn video | `glossary.json` |
| S4 `translate` | dịch sang vi / en | `translations.<lang>.json` |
| S5 `render` | xuất .srt / .ass | `output/` |

`asr.json` là nơi neo của cả pipeline:

```json
{
  "tokens": [
    {"i": 0, "text": "今", "start": 1.24, "end": 1.36, "punct_after": null},
    {"i": 10, "text": "腐", "start": 6.70, "end": 6.90, "punct_after": "。"}
  ],
  "raw_segments": [{"id": 0, "start": 1.24, "end": 6.90, "token_range": [0, 11]}],
  "vad_speech": [[1.20, 6.95]]
}
```

`tokens` chỉ chứa ký tự **có timestamp**. Dấu câu do `ct-punc` sinh không có
timestamp riêng nên treo vào `punct_after` của token liền trước. Nhờ vậy hai
chuỗi tách bạch hoàn toàn: chuỗi để align/gửi LLM là `concat(text)`, chuỗi để
hiển thị là `concat(text + punct_after)`.

### Thêm engine ASR khác

Implement `ASREngine` trong `src/zhsub/asr/base.py`. Toàn bộ phép cộng offset khi
chia chunk nằm ở đó dưới dạng hàm thuần (`plan_chunks`, `shift_output`,
`merge_outputs`), tách khỏi engine, nên test được bằng fake engine — không cần
GPU, không cần audio thật. v1 cố ý chỉ có FunASR; `bench/whisper_runner.py` là
script dùng một lần, không phải một `ASREngine`.

---

## Test

```bash
uv run pytest              # tất cả
uv run pytest -m "not slow"  # bỏ qua test sinh audio 45 phút
```

Test đáng chú ý: `tests/test_chunk_offset.py` dựng WAV 45 phút thật, cắt bằng
ffmpeg và kiểm tra mọi timestamp trả về đều **tuyệt đối so với đầu file**. Đây là
bất biến quan trọng nhất của S1 — sai ở đây thì mọi stage sau đều lệch mà không
có cách nào phát hiện.

---

## Không nằm trong phạm vi

Không TTS, không lồng tiếng, không voice cloning, không burn-in phụ đề vào video,
không dựng video output, không web UI.
