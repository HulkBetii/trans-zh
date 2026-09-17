"""Generator for YouTube Upload Kit tailored for US and Vietnamese YouTube markets.

Specialized for Aviation Disasters, Air Crash Investigations & Flight Safety,
as well as True Crime and Investigative Documentaries.

Generates high-CTR titles, structured SEO descriptions with precision timestamps,
tag lists (<500 chars), pinned engagement comments, and cinematic AI thumbnail prompts.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .subtitle import Cue

log = logging.getLogger(__name__)

AVIATION_KEYWORDS = (
    "flight", "airplane", "aircraft", "dc-10", "boeing", "airbus", "mcdonnell",
    "aviation", "ntsb", "faa", "captain", "pilot", "cockpit", "hydraulics",
    "runway", "airport", "engine", "throttle", "air crash", "disaster",
    "sioux", "haynes", "fitch", "records", "dvorak", "general electric",
    "hàng không", "máy bay", "phi công", "buồng lái", "thủy lực", "động cơ", "tai nạn máy bay",
)


def format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS string."""
    secs = int(max(0, seconds))
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    rem_secs = secs % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{rem_secs:02d}"
    return f"{minutes:02d}:{rem_secs:02d}"


def _clean_cue_text(text: str, max_chars: int = 55, lang: str = "en") -> str:
    """Clean subtitle cue text into a readable chapter title."""
    cleaned = re.sub(r"[\r\n\t]+", " ", text).strip()
    cleaned = re.sub(r"^[\d\.\s\-\,\:\;\!\?]+", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rsplit(" ", 1)[0]
    default_label = "Key Sequence & Progression" if lang.startswith("en") else "Diễn biến tiếp theo"
    return cleaned or default_label


def extract_chapters(
    cues: list[Cue],
    duration_sec: float,
    count: int = 8,
    lang: str = "vi",
) -> list[tuple[str, str]]:
    """Extract narrative milestone chapters from actual subtitle cues."""
    is_en = lang.lower().startswith("en")
    if not cues:
        if is_en:
            return [
                ("00:00", "Introduction & Flight Background"),
                ("05:00", "Emergency In Flight & Initial Failure"),
                ("15:00", "Crew Actions & Emergency Response"),
                ("25:00", "NTSB Findings & Aviation Safety Legacy"),
            ]
        return [
            ("00:00", "Mở đầu vụ việc & Bối cảnh"),
            ("05:00", "Diễn biến sự cố khẩn cấp"),
            ("15:00", "Nỗ lực cứu hộ & Điều tra"),
            ("25:00", "Kết luận & Bài học an toàn"),
        ]

    fractions = [0.0, 0.10, 0.22, 0.35, 0.50, 0.65, 0.78, 0.90]
    chapters: list[tuple[str, str]] = []
    used_cues: set[int] = set()

    for i, frac in enumerate(fractions):
        target_sec = duration_sec * frac
        best_idx = 0
        min_diff = float("inf")
        for idx, cue in enumerate(cues):
            diff = abs(cue.start - target_sec)
            if diff < min_diff:
                min_diff = diff
                best_idx = idx

        if best_idx in used_cues and i > 0:
            continue
        used_cues.add(best_idx)

        cue = cues[best_idx]
        ts = format_timestamp(0.0 if i == 0 else cue.start)
        desc = _clean_cue_text(cue.text, lang=lang)

        if i == 0:
            desc = "Introduction & Departure Background" if is_en else "Mở đầu vụ việc & Bối cảnh ban đầu"
        elif i == len(fractions) - 1:
            desc = f"NTSB Findings & Safety Legacy: {desc}" if is_en else f"Kết luận & Bài học: {desc}"

        chapters.append((ts, desc))

    return chapters


def _remove_vietnamese_accents(text: str) -> str:
    """Remove diacritics for non-accented search tags."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    without = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return without.replace("đ", "d").replace("Đ", "D")


def _is_aviation_context(glossary: dict | None, cues: list[Cue], title: str) -> bool:
    """Detect if the content belongs to the Aviation Disaster niche."""
    content_samples = [title.lower()]
    if glossary:
        for t in glossary.get("terms", []):
            content_samples.append(t.get("zh", "").lower())
            content_samples.append(t.get("en", "").lower())
            content_samples.append(t.get("vi", "").lower())
    for c in cues[:30]:
        content_samples.append(c.text.lower())
    
    joined = " ".join(content_samples)
    matches = sum(1 for kw in AVIATION_KEYWORDS if kw in joined)
    return matches >= 3


def build_youtube_upload_kit_en(
    title: str,
    duration_sec: float,
    cues: list[Cue],
    glossary: dict | None = None,
) -> str:
    """Construct US-standard YouTube Upload Kit in English (Aviation Disasters & Documentaries)."""
    glossary = glossary or {}
    terms = glossary.get("terms", [])
    
    person_names = [t.get("en") or t.get("vi") for t in terms if t.get("type") == "person" and (t.get("en") or t.get("vi"))]
    org_names = [t.get("en") or t.get("vi") for t in terms if t.get("type") == "org" and (t.get("en") or t.get("vi"))]
    key_terms = [t.get("en") or t.get("vi") for t in terms if t.get("type") == "term" and (t.get("en") or t.get("vi"))]

    is_aviation = _is_aviation_context(glossary, cues, title)
    
    flight_name = "United Airlines Flight 232" if any("232" in str(x) or "haynes" in str(x).lower() for x in person_names + key_terms + [title]) else (title or "Aviation Disaster")
    aircraft_type = "McDonnell Douglas DC-10" if any("dc-10" in str(x).lower() or "douglas" in str(x).lower() for x in org_names + key_terms) else "Commercial Airliner"
    lead_pilot = person_names[0] if person_names else "Captain Al Haynes"
    
    chapters = extract_chapters(cues, duration_sec, lang="en")
    duration_str = format_timestamp(duration_sec)
    timestamps_block = "\n".join([f"{ts} {desc}" for ts, desc in chapters])

    sample_texts = [c.text for c in cues[:8] if len(c.text.strip()) > 15]
    story_hook = " ".join(sample_texts[:3]) if sample_texts else f"An in-depth documentary investigation into the harrowing events of {flight_name}."

    if is_aviation:
        title_1 = f"No Controls at 30,000 Feet: The Miracle of {flight_name}"
        title_2 = f"When All 3 Hydraulic Lines Failed: The {aircraft_type} Disaster"
        title_3 = f"How 4 Pilots Steered a Crash-Bound Airliner with Just Engine Throttle"
        title_4 = f"The 1-in-a-Billion Engine Flaw That Crippled {flight_name}"
        title_5 = f"{flight_name}: Total Hydraulic Failure & Miracle Crash Landing"
    else:
        title_1 = f"The Unsolved Mystery: What Really Happened to {lead_pilot}?"
        title_2 = f"Vanished Without a Trace: The True Story Behind {title}"
        title_3 = f"15 Years Later: The Bizarre Clue That Reopened the Investigation"
        title_4 = f"The Impossible Disappearance: Complete Case Breakdown"
        title_5 = f"{title}: The Investigation That Shocked Investigators"

    if is_aviation:
        tag_list = [
            "united airlines flight 232", "flight 232", "dc-10 crash", "sioux city crash",
            "air crash investigation", "aviation disaster", "plane crash documentary",
            "al haynes", "dennis fitch", "total hydraulic failure", "differential thrust landing",
            "ntsb investigation", "aviation safety", "cockpit voice recorder", "mayday air disaster",
            "mcdonnell douglas dc-10", "aviation human factors", "crew resource management",
            "flight safety", "aviation documentary", "emergency landing"
        ]
    else:
        tag_list = [
            "true crime documentary", "unsolved mystery", "investigation breakdown",
            "disappearance", "police report", "cold case", "documentary 2026",
            "survival story", "real life mystery", "investigative journalism"
        ]
        if lead_pilot:
            tag_list.append(lead_pilot.lower())
    
    tags_selected = []
    total_len = 0
    for tag in tag_list:
        tag_len = len(tag) + 2
        if total_len + tag_len <= 480:
            tags_selected.append(tag)
            total_len += tag_len
    tags_str = ", ".join(tags_selected)

    kit_content = f"""================================================================================
YOUTUBE UPLOAD KIT (US / GLOBAL ENGLISH): {flight_name.upper()}
Duration: {duration_str} ({int(duration_sec)}s) | Language: English (US SEO Optimized)
Niche: Aviation Disasters, Air Crash Investigations & Flight Safety
================================================================================

[1. TOP 5 HIGH-CTR TITLE FORMULAS (Choose the best fit for your channel style)]
★ Choice 1 (Recommended - The Impossible Scenario & Suspense Hook):
{title_1}

Choice 2 (Mechanical Breakdown & Catastrophic Failure Focus):
{title_2}

Choice 3 (Human Heroism & Crew Resource Management Angle):
{title_3}

Choice 4 (Engineering Mystery & NTSB Investigation Angle):
{title_4}

Choice 5 (Search & Mobile-Optimized Direct Keyword Match - Under 60 chars):
{title_5}

--------------------------------------------------------------------------------
[2. VIDEO DESCRIPTION & CHAPTER TIMESTAMPS (Copy & Paste directly into YouTube)]
{title_1}

On July 19, 1989, an unimaginable catastrophe struck at 37,000 feet. A sudden uncontained engine explosion instantly severed all three independent hydraulic lines on a widebody airliner, leaving the cockpit crew with zero conventional flight controls. This is the complete, minute-by-minute investigation into one of the most astonishing survival stories in aviation history.

✈️ FLIGHT & INCIDENT FACTSHEET:
• Incident: {flight_name}
• Aircraft: {aircraft_type}
• Flight Crew: {lead_pilot} and Flight Crew
• Key Challenge: Complete loss of all flight control surfaces (ailerons, elevators, rudder)
• Emergency Landing Site: Sioux Gateway Airport, Sioux City, Iowa

📖 DOCUMENTARY SYNOPSIS:
{story_hook}

When all hydraulic fluid drained within seconds, conventional aerodynamics became impossible. Discover how the flight crew pioneered differential engine throttling in real-time, how air traffic controllers cleared the skies, and the critical safety reforms mandated by the NTSB that transformed modern commercial aviation.

⏱️ TIMESTAMPS & CHAPTER MARKERS (Key Moments):
{timestamps_block}

🛠️ KEY AVIATION SAFETY LESSONS & LEGACY:
1. Crew Resource Management (CRM): Establishing collaborative cockpit communication during extreme emergencies.
2. Redundant Hydraulic Isolation Valves: Preventing single-point catastrophic fluid loss across all circuits.
3. Enhanced Titanium Inspection Standards: Revolutionizing manufacturing quality control for high-stress turbine disks.

⚠️ OFFICIAL INVESTIGATION CITATIONS & DISCLAIMER:
This documentary is produced for educational, historical, and aviation safety analysis purposes. Data and factual reconstructions are based upon official reports published by the National Transportation Safety Board (NTSB Accident Report), the Federal Aviation Administration (FAA), and Cockpit Voice Recorder (CVR) transcripts.
Fair Use Notice: This video contains commentary, analysis, and educational critique under Section 107 of the US Copyright Act 1976.

#AviationSafety #AirCrashInvestigation #Flight232 #NTSB #DC10 #AviationDocumentary #MaydayAirDisaster #AviationHistory

--------------------------------------------------------------------------------
[3. SEO TAGS LIST (Copy & Paste directly into YouTube Studio Tags box - {len(tags_str)} chars)]
{tags_str}

================================================================================
[4. 3 CINEMATIC AI THUMBNAIL CONCEPTS & MASTER PROMPTS (Midjourney v6 / DALL-E 3)]
Instructions:
1. Open ChatGPT (GPT-4o / DALL-E 3) or Midjourney v6.
2. Attach any reference cockpit/aircraft image (@image / Image 1).
3. Copy and paste the Master Prompt below to generate a high-converting 16:9 thumbnail with high-contrast text overlay.
================================================================================

▶ CONCEPT 1: COCKPIT POV & EMERGENCY CONTROL FAILURE (RECOMMENDED - MAXIMUM CTR)
• Visual Strategy: First-person cockpit point-of-view looking over the pilots' shoulders. Warning alarms flashing red, hydraulic pressure gauges pegged at zero, the flight yoke turning uselessly while the captain grips the throttle levers. Ahead through the cockpit windshield, Sioux City runway 22 looms closer amidst emergency smoke trails.
• BAKED TEXT OVERLAY ON THUMBNAIL: "NO CONTROLS LEFT!"
• MASTER PROMPT (Copy & Paste):
--------------------------------------------------------------------------------
Create a photorealistic, cinematic 16:9 widescreen YouTube thumbnail for an aviation disaster documentary, referencing the cockpit context from @image.

[COCKPIT DRAMA & PERSPECTIVE]:
Wide-angle first-person POV shot inside the cockpit of a commercial widebody airliner (DC-10) during a severe emergency. The pilot's hands firmly grip the center throttle levers in desperate concentration. The flight control yoke is turned sharply but slack. Instrument panels glow with intense amber and red MASTER WARNING and HYDRAULIC PRESSURE LOW annunciator lights.

[WINDSHIELD VIEW & EXTERIOR]:
Through the cockpit windshield, a distant runway is visible through hazy twilight sky with rising emergency smoke, conveying high-speed descent and imminent crash landing tension.

[TEXT OVERLAY DIRECTLY ON THUMBNAIL - CRITICAL]:
Render massive, high-contrast 3D typography baked cleanly into the lower-right third of the image:
"NO CONTROLS LEFT!"
The font must be ultra-bold industrial sans-serif in vivid warning yellow with a sharp black outer stroke and deep cinematic drop shadow, engineered for maximum readability on mobile devices.

[LIGHTING & COLOR GRADE]:
Dark, suspenseful cockpit atmosphere with moody blue-gray tones contrasted sharply against vibrant red and amber dashboard warning lights. Ultra-sharp 8k resolution, cinematic grain, high click-through rate aesthetic.
--------------------------------------------------------------------------------

▶ CONCEPT 2: AERIAL VIEW - CRIPPLED TRIJET & SEVERED HYDRAULICS
• Visual Strategy: Dramatic exterior aerial shot of the DC-10 banking hard. Tail engine #2 is visibly damaged with trailing smoke and vaporized red hydraulic fluid mist blowing back across the fuselage.
• BAKED TEXT OVERLAY ON THUMBNAIL: "ALL 3 LINES GONE!"
• MASTER PROMPT (Copy & Paste):
--------------------------------------------------------------------------------
Create a hyper-realistic, dramatic 16:9 cinematic YouTube thumbnail of a commercial trijet airliner in extreme distress at golden hour.

[AIRCRAFT EXTERIOR & DAMAGE]:
A McDonnell Douglas DC-10 trijet airliner banked steeply at a 38-degree angle in dramatic flight. The central tail-mounted engine (#2) shows catastrophic fan damage with trailing white vapor and glowing metal debris. Fine streams of atomized red hydraulic fluid spray outward from the empennage into the slipstream.

[ENVIRONMENT & SKY]:
Dramatic sunset sky with towering dark storm clouds and warm golden rim light hitting the top of the polished fuselage. Below, the patchwork green and gold agricultural fields of the American Midwest.

[TEXT OVERLAY DIRECTLY ON THUMBNAIL - CRITICAL]:
Render bold, 3D distressed typography in the upper-left quadrant:
"ALL 3 LINES GONE!"
The typography must be stark white and hazard yellow with a thick black drop shadow, sharp and immediately legible.

[COLOR & MOOD]:
High cinematic contrast, rich warm amber and cold steel blue color grading, 8k photographic detail, intense documentary realism.
--------------------------------------------------------------------------------

▶ CONCEPT 3: SIOUX CITY RUNWAY CRASH & MIRACLE SURVIVAL ACTION
• Visual Strategy: Action-packed wide shot of the crash landing on the runway with rescue fire engines speeding, smoke and fireball in the distance, juxtaposed with the miracle of survivors escaping.
• BAKED TEXT OVERLAY ON THUMBNAIL: "184 SURVIVED?!"
• MASTER PROMPT (Copy & Paste):
--------------------------------------------------------------------------------
Create an intense, cinematic 16:9 documentary YouTube thumbnail depicting a historic airport emergency crash landing and rescue operation.

[SCENE & ACTION]:
A distant runway of a regional airport with emergency fire trucks and ambulances with flashing red and blue lights speeding along the tarmac toward a rising plume of dark smoke and fire on the airfield perimeter. Thick dust and heat distortion shimmer above the pavement.

[TEXT OVERLAY DIRECTLY ON THUMBNAIL - CRITICAL]:
Render huge, high-impact 3D block typography in the lower portion of the image:
"184 SURVIVED?!"
The text must feature bright electric yellow-orange lettering with thick black border outlines, maximum contrast, high CTR visual hook.

[LIGHTING & GRADE]:
Dramatic late afternoon lighting, gritty documentary color grading, cinematic realism, 8k resolution.
--------------------------------------------------------------------------------

================================================================================
[5. PINNED ENGAGEMENT COMMENT (Pin this to the top of the comment section)]
Discussion Prompt:
"If you were in the cockpit of Flight 232 facing total hydraulic loss at 37,000 feet, what would be the most difficult decision to make? In your opinion, did the implementation of Crew Resource Management (CRM) make the difference between complete disaster and saving 184 lives?

Share your technical perspective below — we read and highlight the most insightful aviation analyses!"
================================================================================
"""
    return kit_content


def build_youtube_upload_kit_vi(
    title: str,
    duration_sec: float,
    cues: list[Cue],
    glossary: dict | None = None,
) -> str:
    """Construct Vietnamese-market YouTube Upload Kit."""
    glossary = glossary or {}
    terms = glossary.get("terms", [])
    style = glossary.get("style", {})

    person_names = [t.get("vi") or t.get("en") for t in terms if t.get("type") == "person" and (t.get("vi") or t.get("en"))]
    org_names = [t.get("vi") or t.get("en") for t in terms if t.get("type") == "org" and (t.get("vi") or t.get("en"))]
    key_terms = [t.get("vi") or t.get("en") for t in terms if t.get("type") == "term" and (t.get("vi") or t.get("en"))]

    main_person = person_names[0] if person_names else "Nhân vật chính"
    is_aviation = _is_aviation_context(glossary, cues, title)

    sample_texts = [c.text for c in cues[:10] if len(c.text.strip()) > 15]
    story_hook = " ".join(sample_texts[:3]) if sample_texts else title

    chapters = extract_chapters(cues, duration_sec, lang="vi")
    duration_str = format_timestamp(duration_sec)
    timestamps_block = "\n".join([f"{ts} - {desc}" for ts, desc in chapters])

    if is_aviation:
        title_1 = f"Kỳ Tích Hàng Không: Chiếc Máy Bay Mất Hoàn Toàn Hệ Thống Lái | {title}"
        title_2 = f"Thảm Họa Hàng Không: Nổ Động Cơ Đuôi & 44 Phút Sinh Tử Của Phi Hành Đoàn"
        title_3 = f"Giải Mã Vụ Rơi Máy Bay Kỳ Lạ: Kỳ Tích Sống Sót 184 Người Giữa Vụ Nổ"
        title_4 = f"Khi Toàn Bộ 3 Đường Thủy Lực Đứt Lìa: Bí Mật Điều Tra Của NTSB"
        title_5 = f"{title}: Cuộc Hạ Cánh Khẩn Cấp Chấn Động Lịch Sử Hàng Không"
    else:
        title_1 = f"Kỳ Án 15 Năm: Cao Thủ Sinh Tồn Mất Tích Bí Ẩn | {main_person}"
        title_2 = f"Mẩu Giấy Kỳ Lạ Và Sự Biến Mất Không Dấu Vết Của {main_person} | Kỳ Án Có Thật"
        title_3 = f"Top 1 Kỳ Án Mất Tích: Sự Biến Mất Không Lời Giải Của {main_person}"
        title_4 = f"Hàng Nghìn Người Lùng Sục Vẫn Bốc Hơi: Bí Ẩn Vụ Án {main_person}"
        title_5 = f"Kỳ Án {main_person}: 15 Năm Biến Mất Bí Ẩn Giữa Rừng Tử Thần"

    base_tags = [
        "thảm họa hàng không", "điều tra tai nạn", "kỳ án", "hồ sơ vụ án",
        "giải mã tai nạn", "khám phá bí ẩn", "phim tài liệu", "hàng không",
        "phi công", "buồng lái", "bản dịch chuẩn", "thuyết minh chuẩn"
    ]
    if main_person and main_person != "Nhân vật chính":
        base_tags.extend([main_person.lower(), f"vụ án {main_person.lower()}"])
    for org in org_names[:3]:
        base_tags.append(org.lower())
    for kt in key_terms[:4]:
        base_tags.append(kt.lower())

    all_tags = []
    for tag in base_tags:
        all_tags.append(tag)
        no_acc = _remove_vietnamese_accents(tag)
        if no_acc != tag and no_acc not in all_tags:
            all_tags.append(no_acc)

    tags_str = ", ".join(all_tags)

    if is_aviation:
        thumb_block = """▶ CONCEPT 1: BUỒNG LÁI SINH TỬ & BÁO ĐỘNG ĐỎ (KHUYÊN DÙNG SỐ 1 - SIÊU CLICKBAIT)
• Ý nghĩa thị giác: Góc nhìn từ phía sau cơ trưởng trong buồng lái, đèn báo động nhấp nháy đỏ rực, cần lái mất tác dụng, phi công ghì chặt tay ga nhìn về đường băng mù mịt khói lửa phía trước.
• TEXT CHỮ TẠO TRỰC TIẾP TRÊN THUMBNAIL: "MẤT HẾT HỆ THỐNG LÁI!"
• MASTER PROMPT CHO CHATGPT / DALL-E 3 (Copy dán kèm @image):
--------------------------------------------------------------------------------
Create a cinematic, photorealistic 16:9 widescreen YouTube thumbnail for a dramatic aviation documentary.

[COCKPIT DRAMA]:
POV perspective inside the cockpit of a commercial airliner during severe emergency. Captain gripping the throttle levers with high intensity. Instrument panels glowing with bright red and amber WARNING alarms.

[TEXT OVERLAY DIRECTLY ON THUMBNAIL - CRITICAL]:
Render bold, 3D high-contrast typography in the lower-right quadrant:
"MẤT HẾT HỆ THỐNG LÁI!"
The letters must be vivid warning yellow with thick black outline and drop shadow, perfectly legible on mobile screens.

[ATMOSPHERE & LIGHTING]:
Dramatic lighting, intense red cabin warning light reflections, 8k resolution, cinematic color grading.
--------------------------------------------------------------------------------"""
    else:
        thumb_block = """▶ CONCEPT 1: CHÂN DUNG ĐỐI LẬP & CUNG ĐƯỜNG ĐỨT ĐOẠN (KHUYÊN DÙNG SỐ 1 - SIÊU CLICKBAIT)
• Ý nghĩa thị giác: Bố cục chia đôi hoặc lồng ghép. Bên trái là chân dung chân thực của nhân vật dựa theo ảnh đính kèm (@image), với nét mặt trầm tư, u ám dưới ánh đèn ven sáng. Bên phải là bản đồ địa hình 3D núi rừng hiểm trở với vạch đỏ hành trình đột ngột đứt gãy và tan biến vào một vực thẳm sương mù tối tăm.
• TEXT CHỮ TẠO TRỰC TIẾP TRÊN THUMBNAIL: "15 NĂM BỐC HƠI!" (hoặc "BIẾN MẤT BÍ ẨN!")
• MASTER PROMPT CHO CHATGPT (Copy dán kèm @image):
--------------------------------------------------------------------------------
Create a cinematic, photorealistic 16:9 widescreen YouTube thumbnail for a true-crime investigative documentary, analyzing and strictly referencing the attached image (@image / Image 1).

[IMAGE REFERENCE - CRITICAL]:
Analyze the attached reference image (@image / Image 1). Carefully identify the central subject. Replicate likeness and recognizable features on the left side of the composition with stunning photographic fidelity under dramatic cinematic rim-lighting.

[BACKGROUND & SCENE]:
Behind and to the right of the subject, depict a terrifying, hyper-detailed 3D aerial topographical view of rugged, desolate mountains with steep dark rocky cliffs and eerie winding mountain trails shrouded in twilight mist. A glowing vivid red hiking route line traces across the jagged ridge, then abruptly shatters, snaps, and vanishes into a shadowy abyss.

[TEXT OVERLAY DIRECTLY ON THUMBNAIL - CRITICAL]:
Render large, bold, high-contrast 3D cinematic Vietnamese typography baked directly into the graphic:
"15 NĂM BỐC HƠI!"
The letters must be in vibrant golden-yellow with a thick, crisp black drop shadow and outer outline, slightly weathered texture, ultra-sharp and perfectly legible on small mobile screens.

[ATMOSPHERE & LIGHTING]:
Chilling true-crime atmosphere, cold slate-gray and foggy blue tones contrasting against the warm amber highlight. Ultra-high definition, 8k resolution, cinematic color grading, maximum CTR impact.
--------------------------------------------------------------------------------

▶ CONCEPT 2: MẨU GIẤY CỨU SINH & BÓNG NGƯỜI VÀO VỰC TỬ THẦN
• Ý nghĩa thị giác: Cận cảnh góc nhìn POV soi đèn pin vào mẩu giấy note cứu sinh nhàu nát dán trên vách đá rêu phong.
• TEXT CHỮ TẠO TRỰC TIẾP TRÊN THUMBNAIL: "MẨU GIẤY BÍ ẨN!"
• MASTER PROMPT CHO CHATGPT (Copy dán kèm @image):
--------------------------------------------------------------------------------
Create an intense, hyper-suspenseful 16:9 YouTube thumbnail in cinematic true-crime documentary aesthetic, referencing the character context from the attached image (@image / Image 1).

[FOREGROUND - THE SUSPENSE HOOK]:
Extreme close-up point-of-view shot. A bright, tactical golden-white flashlight beam cuts through misty darkness, directly spotlighting a crumpled, weathered handwritten distress note taped onto a cold, wet, mossy rock cliff.

[TEXT OVERLAY DIRECTLY ON THUMBNAIL - CRITICAL]:
Render bold, 3D distressed typography directly onto the graphic in the upper-center or upper-right area:
"MẨU GIẤY BÍ ẨN!"
The font must be ultra-bold sans-serif, vivid bright yellow with heavy black borders and a dramatic drop shadow, perfectly crisp, legible, and striking.
--------------------------------------------------------------------------------

▶ CONCEPT 3: CHIẾN DỊCH TÌM KIẾM KHỔNG LỒ & SỰ BẤT LỰC
• Ý nghĩa thị giác: Lồng ghép ảnh chân dung nhân vật từ @image trong khung thẻ bài nhỏ ở góc trên, đối lập với toàn cảnh vực thẳm hiểm trở nơi hàng chục luồng đèn pha cứu hộ rọi ngang dọc.
• TEXT CHỮ TẠO TRỰC TIẾP TRÊN THUMBNAIL: "TÌM KIẾM VÔ VỌNG!"
• MASTER PROMPT CHO CHATGPT (Copy dán kèm @image):
--------------------------------------------------------------------------------
Create a high-impact, dramatic 16:9 widescreen YouTube thumbnail for an unsolved true-crime mystery documentary.

[MAIN LANDSCAPE & RESCUE ACTION]:
A dizzying, wide-angle cinematic view looking down into a deep mountain gorge surrounded by vertical cliffs. Dense fog rolls through the ravine at twilight. Multiple searchlights and rescue helicopter beams slice through the mist.

[TEXT OVERLAY DIRECTLY ON THUMBNAIL - CRITICAL]:
Render massive, powerful 3D block typography directly onto the lower portion of the image:
"TÌM KIẾM VÔ VỌNG!"
The text must be styled with fiery yellow-orange color, thick black outline, 3D depth, maximum contrast against the dark background.
--------------------------------------------------------------------------------"""

    kit_content = f"""================================================================================
BỘ KIT UPLOAD YOUTUBE 100% TIẾNG VIỆT: {main_person.upper()} ({title})
Thời lượng: {duration_str} ({int(duration_sec)} giây) | Phụ đề: .srt / .ass
Thị trường: Kênh YouTube Kể Chuyện Thảm Họa / Điều Tra / Kỳ Án Việt Nam
================================================================================

[1. DANH SÁCH TIÊU ĐỀ CLICKBAIT ĐỀ XUẤT (Chọn 1 tiêu đề phù hợp nhất)]
★ Lựa chọn 1 (Đề xuất - Chuẩn Meta Triệu View YouTube VN):
{title_1}

Lựa chọn 2 (Nhấn mạnh chi tiết kỹ thuật & sự cố ngặt nghèo):
{title_2}

Lựa chọn 3 (Top kỳ tích sống sót / giải mã vụ án):
{title_3}

Lựa chọn 4 (Giật gân, khơi gợi tò mò tột đỉnh):
{title_4}

Lựa chọn 5 (Ngắn gọn tối ưu cho giao diện YouTube Mobile):
{title_5}

--------------------------------------------------------------------------------
[2. NỘI DUNG MÔ TẢ & DÒNG THỜI GIAN (Sao chép dán vào phần Mô tả YouTube)]
{title_1}

Chào mừng các bạn đến với kênh! Hôm nay, chúng ta cùng theo dõi hồ sơ điều tra chi tiết về vụ việc: {title}.

📖 TÓM TẮT NỘI DUNG VỤ VIỆC:
{story_hook}

Một cuộc điều tra toàn diện đã được tiến hành cùng nỗ lực phi thường của phi hành đoàn và các cơ quan chức năng. Cùng nhìn lại toàn bộ diễn biến từng phút và những bài học xương máu đắt giá phía sau sự cố này.

⏱️ DÒNG THỜI GIAN & PHÂN ĐOẠN (TIMESTAMPS):
{timestamps_block}

🔔 Đừng quên nhấn LIKE, ĐĂNG KÝ KÊNH và BẬT CHUÔNG THÔNG BÁO để đón xem những video tài liệu hấp dẫn nhất tiếp theo nhé!
💬 Theo bạn, điều gì là mấu chốt quyết định kết cục của vụ việc? Hãy để lại suy luận của bạn dưới phần bình luận!

⚠️ Tuyên Bố Bản Quyền (Disclaimer):
Video được biên tập và thuyết minh tiếng Việt dựa trên tư liệu sự kiện có thật nhằm mục đích cung cấp thông tin, phân tích vụ việc và giáo dục phòng ngừa rủi ro theo nguyên tắc Sử dụng hợp lý (Fair Use).

#hangkhong #thamhoahangkhong #dieutratainan #kyan #khampha #hosoan #youtube

--------------------------------------------------------------------------------
[3. BỘ THẺ TAGS (Sao chép dán trực tiếp vào ô Thẻ/Tags của YouTube Studio)]
{tags_str}

================================================================================
[4. CÁC CONCEPT THUMBNAIL CLICKBAIT & MASTER PROMPT TẠO ẢNH BẰNG AI]
================================================================================

{thumb_block}

================================================================================
[5. GHIM BÌNH LUẬN & GỢI Ý TƯƠNG TÁC (PINNED COMMENT)]
📌 CÂU HỎI THẢO LUẬN TỪ ADMIN:
Theo các bạn sau khi theo dõi toàn bộ diễn biến, yếu tố nào đóng vai trò quan trọng nhất trong việc cứu sống phần lớn hành khách?
1️⃣ Kỹ năng điều khiển lực đẩy động cơ phi thường của phi hành đoàn?
2️⃣ Sự phối hợp nhịp nhàng và kỷ luật trong buồng lái (CRM)?
3️⃣ Phản ứng cứu hộ kịp thời của lực lượng mặt đất tại sân bay?

👉 Hãy thả tim và để lại bình luận phân tích của bạn bên dưới nhé!
================================================================================
"""
    return kit_content


def build_youtube_upload_kit(
    title: str,
    duration_sec: float,
    cues: list[Cue],
    glossary: dict | None = None,
    lang: str = "vi",
) -> str:
    """Construct full text of the YouTube Upload Kit for target language."""
    if lang.lower().startswith("vi"):
        return build_youtube_upload_kit_vi(
            title=title,
            duration_sec=duration_sec,
            cues=cues,
            glossary=glossary,
        )
    return build_youtube_upload_kit_en(
        title=title,
        duration_sec=duration_sec,
        cues=cues,
        glossary=glossary,
    )


def generate_youtube_kit(
    work_dir: Path,
    cues: list[Cue],
    out_path: Path,
    lang: str = "vi",
) -> Path:
    """Read job artifacts from work_dir, build YouTube Upload Kit, and write to out_path."""
    work_dir = Path(work_dir)
    out_path = Path(out_path)

    # 1. Read ingest info
    ingest_path = work_dir / "ingest.json"
    title = work_dir.name
    duration_sec = cues[-1].end if cues else 0.0
    if ingest_path.is_file():
        try:
            ingest_data = json.loads(ingest_path.read_text(encoding="utf-8"))
            title = ingest_data.get("source", {}).get("title") or title
            duration_sec = ingest_data.get("media", {}).get("duration_sec") or duration_sec
        except Exception as e:
            log.warning("Could not read ingest.json for youtube kit: %s", e)

    # 2. Read glossary
    glossary_path = work_dir / "glossary.json"
    glossary_data = None
    if glossary_path.is_file():
        try:
            glossary_data = json.loads(glossary_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Could not read glossary.json for youtube kit: %s", e)

    content = build_youtube_upload_kit(
        title=title,
        duration_sec=duration_sec,
        cues=cues,
        glossary=glossary_data,
        lang=lang,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    log.info("Đã tạo YouTube Upload Kit: %s (lang=%s)", out_path, lang)
    return out_path

