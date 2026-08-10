# zhsub

Pipeline dịch phụ đề tiếng Trung sang tiếng Việt / tiếng Anh. ASR chạy **local**
(không upload audio lên cloud), chỉ khâu dịch mới gọi LLM API.

Hai tiêu chí đánh giá, theo thứ tự ưu tiên:

1. **Bản dịch chuẩn** — đúng nghĩa, tự nhiên, thuật ngữ nhất quán xuyên suốt video.
2. **Timeline sát video gốc** — mỗi dòng phụ đề khớp đúng thời điểm câu đó được nói.

Nguyên tắc bất di bất dịch: **timestamp chỉ đến từ ASR, không bao giờ từ LLM.**
LLM chỉ được trả về text và vị trí ngắt trên chuỗi ký tự; mọi mốc thời gian đều
tra ngược về mảng token của S1.

> **Trạng thái:** S0–S5 đã hoạt động, CLI đầy đủ (`run` / `batch` / `resume` /
> `asr` / `jobs`). Benchmark ở `bench/` đã chọn `paraformer-zh` làm engine ASR.

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

## Cấu hình LLM

```bash
cp zhsub.toml.example zhsub.toml
```

Rồi điền `[llm.segment]` và `[llm.translate]`. Hai khâu cấu hình riêng vì ngắt câu
(S2) là việc cơ học, chỉ khâu dịch (S4) mới cần model mạnh.

Chạy hoàn toàn local, không tốn tiền API:

```bash
winget install Ollama.Ollama
setx OLLAMA_MODELS D:\ollama-models
ollama pull qwen2.5:7b-instruct
```

Rồi trỏ `base_url = "http://localhost:11434/v1"` và `api_key_env = ""`.

**Đặt `OLLAMA_MODELS` ra ngoài ổ hệ thống trước khi pull.** Mặc định Ollama lưu
vào `%USERPROFILE%\.ollama`, và một model 7B chiếm ~5GB.

### ChatGPT web qua Playwright

`provider = "chatgpt_web"` lái thẳng giao diện chatgpt.com bằng một trình duyệt đã
đăng nhập, thay vì gọi API. Không cần API key, chất lượng bằng đúng model bạn đang
trả tiền thuê bao — đổi lại thì:

- Selector là của giao diện web, OpenAI đổi UI lúc nào là hỏng lúc đó.
- Chậm hơn API nhiều, và mỗi lần gọi phải mở một chat mới.
- Tự động hoá web UI là **trái ToS của OpenAI**. Tự cân nhắc.

```bash
uv sync --extra chatgpt-web
uv run playwright install chromium
uv run zhsub chatgpt-login      # đăng nhập tay một lần, profile nhớ phiên
```

Rồi trong `zhsub.toml`:

```toml
[llm.segment]
provider = "chatgpt_web"
model = "chatgpt-web"     # chỉ là nhãn cho khoá cache, không phải model thật
timeout_sec = 300

[llm.translate]
provider = "chatgpt_web"
model = "chatgpt-web"
timeout_sec = 900

[llm.chatgpt_web]
profile_dir = "data/chrome_profile"
```

Chi phí ở đây tính bằng **số lần gọi**, không phải token: mỗi lần tốn ~10–15s phí
cố định cho điều hướng, gõ prompt và poll, dài ngắn không quan trọng lắm. Ngược
hẳn với API, nơi prompt caching hấp thụ phần input lặp lại nên batch nhỏ gần như
miễn phí. Vì thế khi bật `chatgpt_web` thì nên tăng `translate.batch_size` và cân
nhắc tắt `translate.review_pass` — riêng lượt rà soát đã chiếm đúng một nửa số
lần gọi của S4.

Đo trên clip 35 phút (452 câu, dịch cả `vi` lẫn `en`): 60 lần gọi với
`batch_size = 40` + `review_pass = true`, xuống 28 lần với `batch_size = 60` +
`review_pass = false`.

Mọi lần gọi nối đuôi nhau qua **một** cửa sổ trình duyệt, kể cả khi chạy
`zhsub batch -j 4` — nhiều tab cùng gõ vào một tài khoản chỉ làm chạm hạn mức
nhanh hơn. `-j` vẫn tăng tốc phần ASR và render.

Chạm hạn mức tin nhắn thì job **dừng ngay** với lỗi nói rõ nguyên nhân, không thử
lại. Những câu đã dịch xong nằm trong `.cache/`, nên khi hạn mức reset thì
`zhsub resume <job_id> --from translate` chạy tiếp, không mất gì.

Chạy `zhsub chatgpt-login` trước khi dịch. Bỏ qua bước này thì pipeline vẫn tự mở
trình duyệt, nhưng là ở giữa chừng — sau khi ASR đã chạy xong — rồi đứng chờ bạn
đăng nhập.

Cửa sổ trình duyệt phải hiện (headless không qua được Cloudflare), và `[llm.segment]`
với `[llm.translate]` dùng chung một profile: Chromium không mở cùng một thư mục
user-data hai lần.

Muốn tự đăng nhập lại khi hết phiên thì tạo `data/chatgpt_account.json` với
`email` / `password` / `totp_secret` — nhưng mật khẩu sẽ nằm plaintext trên đĩa, và
gặp captcha hay xác minh thiết bị thì vẫn phải làm tay. Không có file này là cấu
hình khuyến nghị.

## Dùng

```bash
# một file
uv run zhsub run video.mp4 --target vi,en --out ./output

# song ngữ: dòng trên tiếng Trung, dòng dưới bản dịch
uv run zhsub run video.mp4 --target vi --bilingual

# cả thư mục, 2 job song song
uv run zhsub batch ./inbox --target vi --concurrency 2

# chạy lại từ một stage sau khi sửa glossary.json
uv run zhsub resume <job_id> --from translate

# chỉ ASR, để kiểm tra timeline
uv run zhsub asr video.mp4 --out raw.srt

# xem trạng thái job
uv run zhsub jobs
```

Nhận file local hoặc URL (Bilibili...). Video cần đăng nhập thì đặt
`ingest.cookies_from_browser` trong `zhsub.toml` — yt-dlp đọc cookie từ browser
bạn đang đăng nhập sẵn; pipeline không bao giờ tự nhập tài khoản hay mật khẩu.

Kết quả trung gian nằm ở `work/<job_id>/`. `job_id` sinh tất định theo nguồn nên
chạy lại cùng một file là dùng lại kết quả cũ thay vì làm lại từ đầu.

### Sửa glossary rồi dịch lại

Đây là vòng lặp quan trọng nhất để bản dịch chuẩn:

1. Chạy `zhsub run` một lần.
2. Mở `work/<job_id>/glossary.json`, sửa tên riêng và thuật ngữ cho đúng. Sửa cả
   `address_terms` nếu xưng hô tiếng Việt chưa hợp.
3. `uv run zhsub resume <job_id> --from translate`

Cache dịch có `glossary_hash` trong khoá nên sửa glossary sẽ tự động dịch lại
đúng những câu bị ảnh hưởng, không dịch lại phần còn nguyên.

### `batch` sau khi mất điện

Chạy lại đúng lệnh cũ. Chỉ job đã `done` bị bỏ qua, nên job kẹt ở `running` do bị
giết vẫn được xử lý lại ngay, **không phải chờ hết ngưỡng heartbeat 5 phút** —
việc thu hồi chỉ để dọn trạng thái cho sạch. Stage nào đã có file JSON hợp lệ thì
không chạy lại, nên resume rẻ.

Đã kiểm chứng: chạy `batch --concurrency 2` trên 3 clip, giết process lúc hai job
đang ở stage `asr`, chạy lại đúng lệnh cũ → 3/3 hoàn tất, mọi job về `done`.

Process bị giết không bao giờ chạy được cleanup, nên trạng thái phải suy ra từ
heartbeat chứ không thể trông vào shutdown hook.

## Dùng như thư viện (nhúng vào app desktop)

CLI chỉ là một cách gọi. Một app desktop cần ba thứ CLI không có: API gọn, báo
tiến độ, và huỷ được giữa chừng.

```python
import threading
from zhsub import Progress, translate_video

cancel = threading.Event()          # bấm Cancel thì cancel.set()

def on_progress(p: Progress) -> None:
    bar.value = p.fraction          # 0..1 cho CẢ job, không phải từng stage
    label.text = f"{p.stage_label} — {p.message}"

result = translate_video(
    "video.mp4",
    targets=["vi", "en"],
    out_dir="./output",
    on_progress=on_progress,
    cancel=cancel,
)
print(result.outputs)               # các file .srt/.ass đã ghi
print(result.glossary_path)         # file để người dùng sửa tay
```

**Huỷ giữa chừng an toàn.** Cancel được kiểm ở ranh giới stage và bên trong các
vòng lặp dài, không bao giờ giữa lúc ghi file. Mỗi stage ghi JSON một lần duy
nhất và ghi nguyên tử, nên job bị huỷ chỉ mất đúng stage đang chạy dở. Riêng khâu
dịch, cache flush sau từng batch nên phần đã trả tiền không mất — gọi lại là chạy
tiếp, không dịch lại.

Báo tiến độ và kiểm huỷ cố ý là **cùng một lời gọi**: mọi chỗ đáng báo tiến độ
đều là chỗ an toàn để dừng, nên nối được thanh progress là tự động có nút Cancel
phản hồi nhanh.

**Vòng lặp sửa glossary từ trong app:**

```python
from zhsub.api import load_glossary, save_glossary, translate_video

gl = load_glossary(result.work_dir)
gl.terms[3].vi = "Nhà tù liên bang Florence"   # người dùng sửa trong UI
save_glossary(result.work_dir, gl)

translate_video("video.mp4", from_stage="translate", ...)   # dịch lại
```

S4 phát hiện glossary đổi và dịch lại, còn cache giữ chi phí ở mức những câu
thực sự bị ảnh hưởng.

### Giới hạn đã biết

- **Thanh tiến độ đứng yên trong lúc chạy ASR.** FunASR không báo tiến độ ra
  ngoài, và file ngắn hơn ngưỡng chunk (mặc định 90 phút) chỉ là một lượt gọi duy
  nhất, nên không có gì để báo ở giữa. Với video 35 phút, bar nằm ở ~2% khoảng 60
  giây. App nên hiện chữ "Đang nhận dạng giọng nói..." thay vì trông vào con số.
- **Hai tiến trình `batch` chạy song song sẽ giẫm chân nhau.** Việc chọn job chỉ
  loại các job `done`, nên tiến trình thứ hai sẽ nhận luôn job mà tiến trình thứ
  nhất đang làm. Dùng `--concurrency` trong MỘT tiến trình, đừng mở hai cửa sổ.
- **S3 vẫn trích cả từ thông thường** vào glossary (监狱, 逃跑) dù prompt đã dặn
  không. Vô hại vì bản dịch không bị ép theo, nhưng làm glossary dài hơn cần thiết.
- **Cột `vi` của glossary đôi khi bị điền tiếng Anh** cho tên cơ quan dài
  ("Florence Federal Correctional Institution"). Sửa tay trong `glossary.json` rồi
  `resume --from translate` là xong — đây đúng là việc mà vòng lặp đó sinh ra để làm.

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

### Vì sao không dùng `sentence_info` của FunASR

FunASR 1.4.1 làm hỏng text trong `sentence_info`. Đo trên mẫu 70 giây đi kèm
model, nó phân kỳ khỏi text cấp trên cùng tại ký tự thứ 297:

```
cấp trên cùng   要聊一天，但是我觉得我刚才说的四个字足够。好，谢谢。
sentence_info   要聊一天但，是我觉得我刚才说的四个字足够好谢谢好非。
```

Dấu câu bị đảo chỗ rồi rơi rụng. Từ đó trở đi mảng `timestamp` riêng của từng câu
không còn khớp với chính text của nó nữa (5/36 câu lệch), và mốc thời gian suy ra
từ đó sai tới **1.7 giây**.

Cặp `text` + `timestamp` ở cấp trên cùng thì thoả một bất biến chính xác, và
`parse_funasr_result` assert đúng bất biến này:

```
len(split_tokens(text)) == len(timestamp)
```

Đo được 333/333 trên mẫu 70 giây và 14/14 trên mẫu 4.5 giây. Nên S1 dựng token từ
cặp cấp trên cùng và **tự ngắt câu** theo dấu câu cộng khoảng lặng, hoàn toàn bỏ
qua `sentence_info`.

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
