import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installMockApi } from "./mockApi";

test("creates a local job, opens its pipeline, and restores its state after refresh", async ({ page }) => {
  const mock = await installMockApi(page, { initialJob: false });

  await page.goto("/");

  await expect(page).toHaveURL(/\/new$/);
  await expect(page.getByRole("heading", { level: 1, name: /Biến một nguồn tiếng Trung/ })).toBeVisible();
  await page.getByLabel("Đường dẫn tuyệt đối").fill("D:\\Media\\episode-01.mp4");
  await page.getByRole("button", { name: "Tạo và bắt đầu" }).click();

  await expect(page).toHaveURL(/\/jobs\/job-001\/pipeline$/);
  await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
  await expect(page.locator(".live-run-head strong")).toHaveText("Đang chờ GPU");
  await expect.poll(() => mock.requests.createJob?.source.value).toBe("D:\\Media\\episode-01.mp4");

  await page.reload();

  await expect(page).toHaveURL(/\/jobs\/job-001\/pipeline$/);
  await expect(page.locator(".live-run-head strong")).toHaveText("Đang chờ GPU");
  expect(mock.requests.createJob).toMatchObject({
    source: { kind: "local", value: "D:\\Media\\episode-01.mp4" },
    targets: ["vi"],
    formats: ["srt"],
    bilingual: false,
  });
});

test("saves glossary changes before requesting retranslation", async ({ page }) => {
  const mock = await installMockApi(page);
  await page.goto("/jobs/job-001/glossary");

  await expect(page.getByRole("heading", { name: "Văn phong" })).toBeVisible();
  await page.getByLabel("Sắc thái lời thoại").fill("Tự nhiên, điềm tĩnh");
  await page.getByRole("button", { name: "Lưu & dịch lại" }).click();

  await expect.poll(() => mock.requests.glossarySaves.length).toBe(1);
  await expect.poll(() => mock.requests.retranslateCalls).toBe(1);
  await expect(page.getByRole("button", { name: "Lưu & dịch lại" })).toHaveCount(0);
  await expect(page.getByLabel("Sắc thái lời thoại")).toHaveValue("Tự nhiên, điềm tĩnh");
  expect(mock.requests.glossarySaves[0]).toMatchObject({
    revision: "glossary-rev-1",
    document: { style: { speech_register: "Tự nhiên, điềm tĩnh" } },
  });
});

test("saves a cue override, renders, and downloads the current artifact", async ({ page }) => {
  const mock = await installMockApi(page);
  await page.goto("/jobs/job-001/subtitles");

  const effectiveEditor = page.getByLabel("Bản hiệu lực");
  await expect(effectiveEditor).toHaveValue("Chào mừng đến với chương trình hôm nay.");
  await effectiveEditor.fill("Bản phụ đề đã biên tập.");
  await expect(page.getByText("1 thay đổi chưa lưu")).toBeVisible();
  await page.getByRole("button", { name: "Lưu & render lại" }).click();

  await expect.poll(() => mock.requests.overrideSaves.length).toBe(1);
  await expect.poll(() => mock.requests.renderCalls).toBe(1);
  await expect(page.getByText("Đã chỉnh tay")).toBeVisible();
  expect(mock.requests.overrideSaves[0]).toEqual({
    revision: "subtitle-rev-1",
    changes: [{ segment_id: 1, text: "Bản phụ đề đã biên tập." }],
  });

  await page.getByRole("link", { name: "Output", exact: true }).click();
  await expect(page.getByText("episode-01.vi.srt", { exact: true })).toBeVisible();
  await expect(page.getByText("Mới nhất", { exact: true })).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Tải episode-01.vi.srt" }).click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toBe("episode-01.vi.srt");
  expect(await download.failure()).toBeNull();
});

test("selects a TTS voice and loads the cue workspace after setup", async ({ page }) => {
  const mock = await installMockApi(page, { ttsEnabled: true });
  await page.goto("/jobs/job-001/pipeline");

  await page.getByRole("link", { name: "Lồng tiếng" }).click();
  await expect(page).toHaveURL(/\/jobs\/job-001\/tts$/);
  await expect(page.getByRole("heading", { name: "Chuẩn bị giọng đọc" })).toBeVisible();
  await expect(page.getByText("Chưa chọn giọng", { exact: true })).toBeVisible();
  await expect(page.getByText("Không có cue khớp bộ lọc hiện tại.")).toBeVisible();

  await page.getByRole("button", { name: "Mở thư viện giọng" }).click();
  await expect(page.getByRole("dialog", { name: "Chọn giọng tiếng Việt" })).toBeVisible();
  await page.getByRole("button", { name: /Minh Quân/ }).click();
  await page.getByRole("button", { name: "Lưu giọng" }).click();

  await expect.poll(() => mock.requests.ttsSettingsSaves.length).toBe(1);
  expect(mock.requests.ttsSettingsSaves[0]).toEqual({
    revision: "tts-rev-1",
    voice_id: "vbee-voice-minh-quan",
  });
  await expect(page.getByText("Minh Quân", { exact: true })).toBeVisible();
  await expect(page.getByText("2 cue", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Văn bản sẽ đọc")).toHaveValue("Chào mừng đến với chương trình hôm nay.");
  await expect(page.getByText("Thiết lập TTS đã được lưu", { exact: true })).toBeVisible();
});

test("tests and persists a masked Settings credential across reload", async ({ page }) => {
  const mock = await installMockApi(page);
  const secret = "ai33-e2e-secret-canary";
  await page.goto("/jobs/job-001/pipeline");

  await page.getByRole("link", { name: "Cài đặt" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole("heading", { level: 1, name: "Kết nối nhà cung cấp AI" })).toBeVisible();

  const ai33Row = page.locator(".credential-row").filter({
    has: page.getByRole("heading", { name: "AI33 / Vbee" }),
  });
  const secretInput = ai33Row.getByLabel("Khóa API AI33 / Vbee");
  await expect(ai33Row.getByText("Chưa có khóa", { exact: true })).toBeVisible();
  await secretInput.fill(secret);
  await ai33Row.getByRole("button", { name: "Kiểm tra" }).click();

  await expect.poll(() => mock.requests.credentialTests.length).toBe(1);
  expect(mock.requests.credentialTests[0]).toEqual({
    credential_id: "ai33",
    payload: { secret },
  });
  await expect(ai33Row.getByText("Kết nối thành công · 37 ms.", { exact: true })).toBeVisible();

  await ai33Row.getByRole("button", { name: "Lưu khóa" }).click();
  await expect.poll(() => mock.requests.credentialSaves.length).toBe(1);
  expect(mock.requests.credentialSaves[0]).toEqual({
    credential_id: "ai33",
    payload: { revision: "settings-rev-1", secret },
  });
  await expect(secretInput).toHaveValue("");
  await expect(ai33Row.getByText("********", { exact: true })).toBeVisible();
  await expect(ai33Row.getByText("Vault", { exact: true })).toBeVisible();
  expect(await page.evaluate((rawSecret) => {
    const textContainsSecret = document.documentElement.textContent?.includes(rawSecret) ?? false;
    const inputContainsSecret = Array.from(document.querySelectorAll("input"))
      .some((input) => input.value.includes(rawSecret));
    return textContainsSecret || inputContainsSecret;
  }, secret)).toBe(false);

  await page.reload();

  const restoredRow = page.locator(".credential-row").filter({
    has: page.getByRole("heading", { name: "AI33 / Vbee" }),
  });
  await expect(restoredRow.getByText("********", { exact: true })).toBeVisible();
  await expect(restoredRow.getByLabel("Khóa API AI33 / Vbee")).toHaveValue("");
  expect(await page.locator("body").textContent()).not.toContain(secret);

  await page.getByRole("link", { name: "Tạo công việc mới", exact: true }).click();
  const ttsReadiness = page.locator(".readiness-item").filter({ hasText: "AI33 / Vbee TTS" });
  await expect(ttsReadiness).toHaveClass(/ready/);
  await expect(ttsReadiness.getByText("Đã cấu hình qua kho bảo mật.", { exact: true })).toBeVisible();
});

test("opens the cue inspector as a mobile bottom sheet", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installMockApi(page);
  await page.goto("/jobs/job-001/subtitles");

  await page.getByRole("button", { name: "Chi tiết" }).click();
  const inspector = page.getByRole("dialog", { name: "Chi tiết cue" });
  await expect(inspector).toBeVisible();
  await expect(inspector).toHaveCSS("position", "fixed");
  const bounds = await inspector.boundingBox();
  expect(bounds).not.toBeNull();
  expect(Math.abs((bounds?.y ?? 0) + (bounds?.height ?? 0) - 844)).toBeLessThanOrEqual(1);

  await inspector.getByRole("button", { name: "Đóng inspector" }).click();
  await expect(inspector).toBeHidden();
});

for (const viewport of [
  { width: 1440, height: 900, label: "desktop" },
  { width: 1280, height: 900, label: "compact desktop" },
  { width: 768, height: 1024, label: "tablet" },
  { width: 390, height: 844, label: "mobile" },
]) {
  test(`keeps the studio usable at the ${viewport.label} viewport`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await installMockApi(page, { ttsEnabled: true });
    await page.goto("/jobs/job-001/pipeline");

    await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Không gian công việc" })).toBeVisible();
    if (viewport.width > 1280) {
      await expect(page.locator(".job-navigator-pane")).toBeVisible();
      await expect(page.locator(".mobile-header")).toBeHidden();
    } else {
      await expect(page.locator(".mobile-header")).toBeVisible();
      await expect(page.locator(".job-navigator-pane")).toBeHidden();
    }

    const horizontalOverflow = await page.evaluate(() =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(horizontalOverflow).toBe(false);

    for (const path of [
      "/new",
      "/settings",
      "/jobs/job-001/pipeline",
      "/jobs/job-001/glossary",
      "/jobs/job-001/subtitles",
      "/jobs/job-001/tts",
      "/jobs/job-001/outputs",
    ]) {
      await page.goto(path);
      await expect(page.locator(".workspace")).toBeVisible();
      expect(await page.evaluate(() =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      )).toBe(false);
      if (viewport.width === 390) {
        const undersized = await page.locator("button:visible, a:visible").evaluateAll((elements) => elements.flatMap((element) => {
          const rect = element.getBoundingClientRect();
          if (rect.width >= 44 && rect.height >= 44) return [];
          return [{ label: element.getAttribute("aria-label") || element.textContent?.trim(), width: rect.width, height: rect.height }];
        }));
        expect(undersized, `${path} có touch target nhỏ hơn 44px`).toEqual([]);
      }
    }
  });
}

test("passes an Axe smoke check on the main production surfaces", async ({ page }) => {
  await installMockApi(page, { ttsEnabled: true });

  for (const path of ["/new", "/jobs/job-001/subtitles", "/settings"]) {
    await page.goto(path);
    await expect(page.locator(".workspace")).toBeVisible();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const seriousViolations = results.violations.filter((violation) =>
      violation.impact === "serious" || violation.impact === "critical",
    );
    expect(seriousViolations, `${path}: ${seriousViolations.map((item) => item.id).join(", ")}`).toEqual([]);
  }
});
