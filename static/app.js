/* ────────────────────────────────────────────────────────────
   AI Video Transcriber · app.js
   ──────────────────────────────────────────────────────────── */

class VideoTranscriber {
  constructor() {
    this.currentTaskId  = null;
    this.eventSource    = null;
    this.taskStreams    = new Map();
    this.taskItems      = new Map();
    this.liveTaskLogs   = new Map();
    this.apiBase        = '/api';
    this.currentTimedTranscript = '';
    this.currentTimedTranslation = '';
    this.currentReviewCues = [];
    this.voiceOverrides = {};
    this.currentDubbedVideoUrl = '';
    this.currentDubbedVideoFilename = '';
    this.dubProgressTaskId = null;
    this.settingsSaveTimer = null;

    /* Smart progress simulation */
    this.sp = {
      enabled: false, current: 0, target: 15,
      lastServer: 0, interval: null, startTime: null, stage: 'preparing'
    };
    this.dubProgressTimer = null;

    this.i18n = {
      en: {
        title:                   'AI Video Transcriber',
        subtitle:                '',
        video_url_placeholder:   'Video URL',
        start_transcription:     'Transcribe',
        download_only:           'Download video only',
        download_video:          'Download Video',
        hard_subtitle_ocr:       'Hard subtitle OCR',
        ocr_video:               'OCR Subtitles',
        ocr_settings:            'Hard Subtitle OCR',
        ocr_language:            'OCR Language',
        ocr_fps:                 'OCR FPS',
        ocr_crop_top:            'Crop top %',
        ocr_crop_bottom:         'Crop bottom %',
        ocr_crop_left:           'Crop left %',
        ocr_crop_right:          'Crop right %',
        ocr_confidence:          'Min confidence',
        settings:                'Settings',
        ai_settings:             'AI Settings',
        subtitle_settings:       'Subtitle Settings',
        recent_history:          'Tasks',
        task_queue:              'Tasks',
        refresh:                 'Refresh',
        media_ready:             'media',
        translation_ready:       'translation',
        model_base_url:          'Model API Base URL',
        model_base_url_placeholder: 'https://openrouter.ai/api/v1',
        api_key:                 'API Key',
        api_key_placeholder:     'sk-...',
        access_token:            'Access token',
        access_token_placeholder: 'Only if APP_AUTH_TOKEN is set on the server',
        fetch_models:            'Fetch',
        model_select:            'Model',
        model_default:           '— use server default —',
        platform_cookies:        'Platform Cookies',
        douyin_cookie:           'Douyin Cookie',
        bilibili_cookie:         'Bilibili Cookie',
        douyin_cookie_file:      'Douyin cookies.txt',
        bilibili_cookie_file:    'Bilibili cookies.txt',
        douyin_cookie_placeholder: 'Paste Douyin Cookie header or document.cookie',
        bilibili_cookie_placeholder: 'Paste Bilibili Cookie header or document.cookie',
        tts_voice_settings:      'TTS Voice Settings',
        speaker_mode:            'Speaker Mode',
        speaker_default:         'One voice',
        speaker_all_male:        'Male voice for all',
        speaker_all_female:      'Female voice for all',
        speaker_auto_gender:     'Auto gender / turns',
        speaker_alternate:       'Alternate male / female',
        tts_style:               'Reading Style',
        dub_audio_mode:          'Dub audio mode',
        dub_audio_replace:       'Replace original audio',
        dub_audio_voiceover:     'Keep original + overlay dub',
        dub_original_volume:     'Original volume',
        dub_include_subtitles:   'Dubbed video subtitles',
        dub_subtitles_off:       'Do not burn subtitles',
        dub_subtitles_on:        'Burn subtitle settings into video',
        tts_merge_seconds:       'Merge TTS under seconds',
        tts_max_fit_speed:       'Max speed-up',
        default_voice:           'Default Voice',
        male_voice:              'Male Voice',
        female_voice:            'Female Voice',
        default_clone:           'Default Clone Clip',
        male_clone:              'Male Clone Clip',
        female_clone:            'Female Clone Clip',
        summary_language:        'Summary Language',
        processing_progress:     'Processing',
        task_log:                'Live log',
        preparing:               'Preparing…',
        transcript_text:         'Transcript',
        intelligent_summary:     'AI Summary',
        translation:             'Translation',
        review_sync:             'Review',
        no_timed_transcript:     'No timestamped transcript is available for this media.',
        download_transcript:     'Transcript',
        download_translation:    'Translation',
        regenerate_translation:  'Regenerate Translation',
        regenerating_translation:'Regenerating translation…',
        regenerate_transcript:   'Regenerate Transcript',
        regenerating_transcript: 'Regenerating transcript…',
        run_hard_subtitle_ocr:   'Hard subtitle OCR',
        running_hard_subtitle_ocr:'Running OCR…',
        resume_task:             'Resume task',
        resuming_task:           'Resuming task…',
        download_summary:        'Summary',
        export_video:            'Export Video',
        export_dubbed_video:     'Export Dubbed Video',
        download_dubbed_video:   'Download Dubbed Video',
        exporting_video:         'Exporting…',
        exporting_dubbed_video:  'Generating voice…',
        dub_progress_prepare:    'Preparing dubbed export...',
        empty_hint:              'Select a task or start a new video.',
        processing:              'Processing…',
        downloading_video:       'Downloading video…',
        parsing_video:           'Parsing video info…',
        transcribing_audio:      'Transcribing audio…',
        optimizing_transcript:   'Optimizing transcript…',
        generating_summary:      'Generating summary…',
        detecting_subtitles:     'Detecting subtitles…',
        subtitle_found:          'Subtitles found! Processing text…',
        no_subtitle:             'No subtitles found, downloading audio…',
        mode_subtitle:           '⚡ Subtitle',
        mode_whisper:            '🎙 Whisper',
        completed:               'Done!',
        error_invalid_url:       'Please enter a valid video URL',
        error_processing_failed: 'Processing failed: ',
        error_douyin_cookie_required: 'Douyin requires a fresh Cookie request header. Paste it in Settings > AI Settings > Douyin Cookie.',
        error_no_download:       'No file available for download',
        error_download_failed:   'Download failed: ',
        error_export_failed:     'Export failed: ',
        error_dub_failed:        'Dub export failed: ',
        error_model_base_url:    'Model API Base URL must be an OpenAI-compatible endpoint, not a video URL',
        fetching_models:         'Fetching models…',
        models_loaded:           (n) => `${n} models loaded`,
        models_error:            'Failed to fetch models',
        upload_or:               'or drop your files',
        upload_formats:          '.mp3 · .mp4 · .wav · .m4a · .webm · .mkv · .ogg · .flac',
        upload_files_btn:        'Upload files',
        error_upload_type:       'Unsupported file type',
        error_upload_empty:      'File is empty',
        error_upload_size:       (mb) => `File exceeds ${mb} MB limit`,
        rename_task:             'Rename task',
        delete_task:             'Delete task',
        rename_task_prompt:      'Rename this video',
        delete_task_confirm:     'Delete this video task and remove related cache/files?',
        preview_voice:           'Preview voice',
      }
    };

    this._installAuthFetch();
    this._initElements();
    this._bindEvents();
    this._loadSettings();
    this._updateTtsProviderFields();
    this._applyEnglishLabels();
    this._updateSubmitModeLabel();
    this._loadRecentTasks();
  }

  /* ── Elements ─────────────────────────────────────────── */
  _initElements() {
    this.form               = document.getElementById('videoForm');
    this.videoUrlInput      = document.getElementById('videoUrl');
    this.submitBtn          = document.getElementById('submitBtn');
    this.downloadOnly       = document.getElementById('downloadOnly');
    this.hardSubtitleOcr    = document.getElementById('hardSubtitleOcr');
    this.summaryLangSel     = document.getElementById('summaryLanguage');
    this.errorBanner        = document.getElementById('errorBanner');
    this.errorMsg           = document.getElementById('errorMsg');
    this.errorResumeTaskBtn = document.getElementById('errorResumeTaskBtn');
    this.emptyState         = document.getElementById('emptyState');
    this.progressPanel      = document.getElementById('progressPanel');
    this.modeBadge          = document.getElementById('modeBadge');
    this.progressStatus     = document.getElementById('progressStatus');
    this.progressFill       = document.getElementById('progressFill');
    this.progressMessage    = document.getElementById('progressMessage');
    this.taskLog            = document.getElementById('taskLog');
    this.taskLogBody        = document.getElementById('taskLogBody');
    this.taskLogCount       = document.getElementById('taskLogCount');
    this.resumeTaskBtn      = document.getElementById('resumeTaskBtn');
    this.resultsPanel       = document.getElementById('resultsPanel');
    this.scriptContent      = document.getElementById('scriptContent');
    this.summaryContent     = document.getElementById('summaryContent');
    this.translationContent = document.getElementById('translationContent');
    this.reviewTabBtn       = document.getElementById('reviewTabBtn');
    this.reviewTitle        = document.getElementById('reviewTitle');
    this.reviewMedia        = document.getElementById('reviewMedia');
    this.cueList            = document.getElementById('cueList');
    this.subtitleMode       = document.getElementById('subtitleMode');
    this.subtitleSource     = document.getElementById('subtitleSource');
    this.subtitleFontSize   = document.getElementById('subtitleFontSize');
    this.subtitlePosition   = document.getElementById('subtitlePosition');
    this.subtitleXOffset    = document.getElementById('subtitleXOffset');
    this.subtitleYOffset    = document.getElementById('subtitleYOffset');
    this.subtitleMergeSeconds = document.getElementById('subtitleMergeSeconds');
    this.subtitleTimeOffset = document.getElementById('subtitleTimeOffset');
    this.subtitleTextColor  = document.getElementById('subtitleTextColor');
    this.subtitleBoxColor   = document.getElementById('subtitleBoxColor');
    this.subtitleOutlineColor = document.getElementById('subtitleOutlineColor');
    this.secondarySubtitleSettings = document.getElementById('secondarySubtitleSettings');
    this.secondarySubtitleSource = document.getElementById('secondarySubtitleSource');
    this.secondarySubtitleFontSize = document.getElementById('secondarySubtitleFontSize');
    this.secondarySubtitleTextColor = document.getElementById('secondarySubtitleTextColor');
    this.secondarySubtitleBoxColor = document.getElementById('secondarySubtitleBoxColor');
    this.secondarySubtitleOutlineColor = document.getElementById('secondarySubtitleOutlineColor');
    this.exportSrtBtn       = document.getElementById('exportSrt');
    this.exportVttBtn       = document.getElementById('exportVtt');
    this.dlScript           = document.getElementById('downloadScript');
    this.dlTranslation      = document.getElementById('downloadTranslation');
    this.regenerateTranslationBtn = document.getElementById('regenerateTranslation');
    this.regenerateTranscriptBtn = document.getElementById('regenerateTranscript');
    this.runHardSubtitleOcrBtn = document.getElementById('runHardSubtitleOcr');
    this.dlSummary          = document.getElementById('downloadSummary');
    this.exportVideoBtn     = document.getElementById('exportVideo');
    this.exportDubbedVideoBtn = document.getElementById('exportDubbedVideo');
    this.dubProgressPanel   = document.getElementById('dubProgressPanel');
    this.dubProgressFill    = document.getElementById('dubProgressFill');
    this.dubProgressStatus  = document.getElementById('dubProgressStatus');
    this.dubProgressMessage = document.getElementById('dubProgressMessage');
    this.translationTabBtn  = document.getElementById('translationTabBtn');
    this.tabBtns            = document.querySelectorAll('.tab-btn');
    this.tabPanes           = document.querySelectorAll('.tab-pane');
    // settings
    this.settingsToggle     = document.getElementById('settingsToggle');
    this.settingsBody       = document.getElementById('settingsBody');
    this.settingsTabBtns    = document.querySelectorAll('.settings-tab');
    this.settingsPanes      = document.querySelectorAll('.settings-pane');
    this.modelBaseUrl       = document.getElementById('modelBaseUrl');
    this.apiKeyInput        = document.getElementById('apiKeyInput');
    this.accessTokenInput   = document.getElementById('accessTokenInput');
    this.fetchModelsBtn     = document.getElementById('fetchModelsBtn');
    this.fetchStatus        = document.getElementById('fetchStatus');
    this.modelSelect        = document.getElementById('modelSelect');
    this.douyinCookie       = document.getElementById('douyinCookie');
    this.bilibiliCookie     = document.getElementById('bilibiliCookie');
    this.douyinCookieFile   = document.getElementById('douyinCookieFile');
    this.bilibiliCookieFile = document.getElementById('bilibiliCookieFile');
    this.ocrLanguage        = document.getElementById('ocrLanguage');
    this.ocrFps             = document.getElementById('ocrFps');
    this.ocrCropTop         = document.getElementById('ocrCropTop');
    this.ocrCropBottom      = document.getElementById('ocrCropBottom');
    this.ocrCropLeft        = document.getElementById('ocrCropLeft');
    this.ocrCropRight       = document.getElementById('ocrCropRight');
    this.ocrConfidence      = document.getElementById('ocrConfidence');
    this.ttsSpeakerMode     = document.getElementById('ttsSpeakerMode');
    this.ttsProvider        = document.getElementById('ttsProvider');
    this.ttsProviderFields  = document.querySelectorAll('[data-tts-provider-field]');
    this.ttsStyle           = document.getElementById('ttsStyle');
    this.elevenLabsApiKey   = document.getElementById('elevenLabsApiKey');
    this.fetchElevenLabsVoicesBtn = document.getElementById('fetchElevenLabsVoicesBtn');
    this.elevenLabsModelId  = document.getElementById('elevenLabsModelId');
    this.elevenLabsDefaultVoiceId = document.getElementById('elevenLabsDefaultVoiceId');
    this.elevenLabsMaleVoiceId = document.getElementById('elevenLabsMaleVoiceId');
    this.elevenLabsFemaleVoiceId = document.getElementById('elevenLabsFemaleVoiceId');
    this.fptApiKey          = document.getElementById('fptApiKey');
    this.fptSpeed           = document.getElementById('fptSpeed');
    this.fptDefaultVoice    = document.getElementById('fptDefaultVoice');
    this.fptMaleVoice       = document.getElementById('fptMaleVoice');
    this.fptFemaleVoice     = document.getElementById('fptFemaleVoice');
    this.previewElevenLabsDefaultVoiceBtn = document.getElementById('previewElevenLabsDefaultVoice');
    this.previewElevenLabsMaleVoiceBtn = document.getElementById('previewElevenLabsMaleVoice');
    this.previewElevenLabsFemaleVoiceBtn = document.getElementById('previewElevenLabsFemaleVoice');
    this.previewFptDefaultVoiceBtn = document.getElementById('previewFptDefaultVoice');
    this.previewFptMaleVoiceBtn = document.getElementById('previewFptMaleVoice');
    this.previewFptFemaleVoiceBtn = document.getElementById('previewFptFemaleVoice');
    this.dubAudioMode       = document.getElementById('dubAudioMode');
    this.dubOriginalVolume  = document.getElementById('dubOriginalVolume');
    this.dubIncludeSubtitles = document.getElementById('dubIncludeSubtitles');
    this.ttsMergeSeconds    = document.getElementById('ttsMergeSeconds');
    this.ttsMaxFitSpeed     = document.getElementById('ttsMaxFitSpeed');
    this.dubTimingScale     = document.getElementById('dubTimingScale');
    this.dubAudioOffset     = document.getElementById('dubAudioOffset');
    this.ttsDefaultVoice    = document.getElementById('ttsDefaultVoice');
    this.ttsMaleVoice       = document.getElementById('ttsMaleVoice');
    this.ttsFemaleVoice     = document.getElementById('ttsFemaleVoice');
    this.previewDefaultVoiceBtn = document.getElementById('previewDefaultVoice');
    this.previewMaleVoiceBtn = document.getElementById('previewMaleVoice');
    this.previewFemaleVoiceBtn = document.getElementById('previewFemaleVoice');
    this.ttsDefaultClone    = document.getElementById('ttsDefaultClone');
    this.ttsMaleClone       = document.getElementById('ttsMaleClone');
    this.ttsFemaleClone     = document.getElementById('ttsFemaleClone');
    this.ttsDefaultCloneStatus = document.getElementById('ttsDefaultCloneStatus');
    this.ttsMaleCloneStatus = document.getElementById('ttsMaleCloneStatus');
    this.ttsFemaleCloneStatus = document.getElementById('ttsFemaleCloneStatus');
    this.fetchIcon          = document.getElementById('fetchIcon');
    this.uploadZone         = document.getElementById('uploadZone');
    this.uploadPickBtn      = document.getElementById('uploadPickBtn');
    this.fileInput          = document.getElementById('fileInput');
    this.recentPanel        = document.getElementById('recentPanel');
    this.recentList         = document.getElementById('recentList');
    this.refreshRecentBtn   = document.getElementById('refreshRecent');
    this.uploadMaxMb        = 200;
    this._allowedUploadExts = new Set(['.txt', '.mp3', '.mp4', '.m4a', '.wav', '.webm', '.mkv', '.ogg', '.flac']);
  }

  /* ── Events ───────────────────────────────────────────── */
  _bindEvents() {
    this.form.addEventListener('submit', (e) => { e.preventDefault(); this._startTranscription(); });
    if (this.downloadOnly) this.downloadOnly.addEventListener('change', () => {
      if (this.downloadOnly.checked && this.hardSubtitleOcr) this.hardSubtitleOcr.checked = false;
      this._updateSubmitModeLabel();
    });
    if (this.hardSubtitleOcr) this.hardSubtitleOcr.addEventListener('change', () => {
      if (this.hardSubtitleOcr.checked && this.downloadOnly) this.downloadOnly.checked = false;
      this._updateSubmitModeLabel();
    });
    if (this.refreshRecentBtn) this.refreshRecentBtn.addEventListener('click', () => this._loadRecentTasks());

    // Settings toggle
    this.settingsToggle.addEventListener('click', () => {
      if (this.settingsBody.classList.contains('open')) this._closeSettings();
      else this._openSettings();
    });
    this.settingsBody.addEventListener('click', (e) => {
      if (e.target === this.settingsBody) this._closeSettings();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.settingsBody.classList.contains('open')) this._closeSettings();
    });
    this.settingsTabBtns.forEach(btn => {
      btn.addEventListener('click', () => this._switchSettingsTab(btn.dataset.settingsTab));
    });

    // Fetch models
    this.fetchModelsBtn.addEventListener('click', () => this._fetchModels());

    // Auto-fetch when both fields filled (debounced)
    const debouncedFetch = this._debounce(() => {
      if (this.modelBaseUrl.value.trim() && this.apiKeyInput.value.trim()) this._fetchModels();
    }, 900);
    this.modelBaseUrl.addEventListener('input', debouncedFetch);
    this.apiKeyInput.addEventListener('input', debouncedFetch);

    // Persist settings
    [
      this.modelBaseUrl,
      this.apiKeyInput,
      this.accessTokenInput,
      this.modelSelect,
      this.summaryLangSel,
      this.douyinCookie,
      this.bilibiliCookie,
      this.ocrLanguage,
      this.ocrFps,
      this.ocrCropTop,
      this.ocrCropBottom,
      this.ocrCropLeft,
      this.ocrCropRight,
      this.ocrConfidence,
      this.ttsSpeakerMode,
      this.ttsStyle,
      this.dubAudioMode,
      this.dubOriginalVolume,
      this.dubIncludeSubtitles,
      this.ttsMergeSeconds,
      this.ttsMaxFitSpeed,
      this.dubTimingScale,
      this.dubAudioOffset,
      this.ttsProvider,
      this.ttsDefaultVoice,
      this.ttsMaleVoice,
      this.ttsFemaleVoice,
      this.elevenLabsApiKey,
      this.elevenLabsModelId,
      this.elevenLabsDefaultVoiceId,
      this.elevenLabsMaleVoiceId,
      this.elevenLabsFemaleVoiceId,
      this.fptApiKey,
      this.fptSpeed,
      this.fptDefaultVoice,
      this.fptMaleVoice,
      this.fptFemaleVoice,
    ].filter(Boolean).forEach(el => {
      el.addEventListener('change', () => this._saveSettings());
    });
    if (this.ttsProvider) {
      this.ttsProvider.addEventListener('change', () => this._updateTtsProviderFields());
    }
    if (this.fetchElevenLabsVoicesBtn) {
      this.fetchElevenLabsVoicesBtn.addEventListener('click', () => this._loadElevenLabsVoices());
    }
    if (this.elevenLabsApiKey) {
      this.elevenLabsApiKey.addEventListener('change', () => {
        if (this.ttsProvider?.value === 'elevenlabs') this._loadElevenLabsVoices();
      });
    }
    if (this.douyinCookieFile && this.douyinCookie) {
      this.douyinCookieFile.addEventListener('change', () => {
        this._persistCookieFileAsText(this.douyinCookieFile, this.douyinCookie);
      });
    }
    if (this.bilibiliCookieFile && this.bilibiliCookie) {
      this.bilibiliCookieFile.addEventListener('change', () => {
        this._persistCookieFileAsText(this.bilibiliCookieFile, this.bilibiliCookie);
      });
    }

    // Tabs
    this.tabBtns.forEach(btn => {
      btn.addEventListener('click', () => this._switchTab(btn.dataset.tab));
    });

    // Downloads
    this.dlScript.addEventListener('click',      () => this._downloadFile('script'));
    this.dlTranslation.addEventListener('click', () => this._downloadFile('translation'));
    if (this.regenerateTranslationBtn) this.regenerateTranslationBtn.addEventListener('click', () => this._regenerateTranslation());
    if (this.regenerateTranscriptBtn) this.regenerateTranscriptBtn.addEventListener('click', () => this._regenerateTranscript());
    if (this.runHardSubtitleOcrBtn) this.runHardSubtitleOcrBtn.addEventListener('click', () => this._runHardSubtitleOcr());
    if (this.resumeTaskBtn) this.resumeTaskBtn.addEventListener('click', () => this._resumeCurrentTask(this.resumeTaskBtn));
    if (this.errorResumeTaskBtn) this.errorResumeTaskBtn.addEventListener('click', () => this._resumeCurrentTask(this.errorResumeTaskBtn));
    this.dlSummary.addEventListener('click',     () => this._downloadFile('summary'));
    this.exportVideoBtn.addEventListener('click', () => this._exportVideo());
    this.exportDubbedVideoBtn.addEventListener('click', () => this._exportDubbedVideo());
    if (this.exportSrtBtn) this.exportSrtBtn.addEventListener('click', () => this._exportSubtitleFile('srt'));
    if (this.exportVttBtn) this.exportVttBtn.addEventListener('click', () => this._exportSubtitleFile('vtt'));
    if (this.previewDefaultVoiceBtn) this.previewDefaultVoiceBtn.addEventListener('click', () => this._previewVoice(this.ttsDefaultVoice, this.previewDefaultVoiceBtn, 'default'));
    if (this.previewMaleVoiceBtn) this.previewMaleVoiceBtn.addEventListener('click', () => this._previewVoice(this.ttsMaleVoice, this.previewMaleVoiceBtn, 'male'));
    if (this.previewFemaleVoiceBtn) this.previewFemaleVoiceBtn.addEventListener('click', () => this._previewVoice(this.ttsFemaleVoice, this.previewFemaleVoiceBtn, 'female'));
    if (this.previewElevenLabsDefaultVoiceBtn) this.previewElevenLabsDefaultVoiceBtn.addEventListener('click', () => this._previewVoice(this.elevenLabsDefaultVoiceId, this.previewElevenLabsDefaultVoiceBtn, 'default'));
    if (this.previewElevenLabsMaleVoiceBtn) this.previewElevenLabsMaleVoiceBtn.addEventListener('click', () => this._previewVoice(this.elevenLabsMaleVoiceId, this.previewElevenLabsMaleVoiceBtn, 'male'));
    if (this.previewElevenLabsFemaleVoiceBtn) this.previewElevenLabsFemaleVoiceBtn.addEventListener('click', () => this._previewVoice(this.elevenLabsFemaleVoiceId, this.previewElevenLabsFemaleVoiceBtn, 'female'));
    if (this.previewFptDefaultVoiceBtn) this.previewFptDefaultVoiceBtn.addEventListener('click', () => this._previewVoice(this.fptDefaultVoice, this.previewFptDefaultVoiceBtn, 'default'));
    if (this.previewFptMaleVoiceBtn) this.previewFptMaleVoiceBtn.addEventListener('click', () => this._previewVoice(this.fptMaleVoice, this.previewFptMaleVoiceBtn, 'male'));
    if (this.previewFptFemaleVoiceBtn) this.previewFptFemaleVoiceBtn.addEventListener('click', () => this._previewVoice(this.fptFemaleVoice, this.previewFptFemaleVoiceBtn, 'female'));
    [this.ttsDefaultClone, this.ttsMaleClone, this.ttsFemaleClone].filter(Boolean).forEach(input => {
      input.addEventListener('change', () => this._updateCloneUsageLabels());
    });
    [
      this.subtitleMode,
      this.subtitleSource,
      this.subtitleFontSize,
      this.subtitlePosition,
      this.subtitleXOffset,
      this.subtitleYOffset,
      this.subtitleMergeSeconds,
      this.subtitleTimeOffset,
      this.subtitleTextColor,
      this.subtitleBoxColor,
      this.subtitleOutlineColor,
      this.secondarySubtitleSource,
      this.secondarySubtitleFontSize,
      this.secondarySubtitleTextColor,
      this.secondarySubtitleBoxColor,
      this.secondarySubtitleOutlineColor,
    ]
      .filter(Boolean)
      .forEach(el => el.addEventListener('input', () => {
        this._updateSubtitlePreview();
        this._saveSettings();
      }));

    if (this.uploadPickBtn && this.fileInput && this.uploadZone) {
      this.uploadPickBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.fileInput.click();
      });
      this.uploadZone.addEventListener('click', (e) => {
        if (e.target === this.uploadPickBtn || this.uploadPickBtn.contains(e.target)) return;
        this.fileInput.click();
      });
      this.fileInput.addEventListener('change', () => {
        const f = this.fileInput.files && this.fileInput.files[0];
        this.fileInput.value = '';
        if (f) this._startFileUpload(f);
      });
      ['dragenter', 'dragover'].forEach((ev) => {
        this.uploadZone.addEventListener(ev, (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.uploadZone.classList.add('dragover');
        });
      });
      this.uploadZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        if (!this.uploadZone.contains(e.relatedTarget)) {
          this.uploadZone.classList.remove('dragover');
        }
      });
      this.uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.uploadZone.classList.remove('dragover');
        const f = e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) this._startFileUpload(f);
      });
    }
  }

  /* ── i18n ─────────────────────────────────────────────── */
  t(key) { return this.i18n.en[key] || key; }

  _applyEnglishLabels() {
    document.documentElement.lang = 'en';
    document.title = this.t('title');

    document.querySelectorAll('[data-i18n]').forEach(el => {
      const v = this.t(el.dataset.i18n);
      if (typeof v === 'string') {
        el.textContent = v;
      }
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const v = this.t(el.dataset.i18nPlaceholder);
      if (typeof v === 'string') el.placeholder = v;
    });
  }

  /* ── Access token ─────────────────────────────────────── */
  /** Server-side APP_AUTH_TOKEN is optional; when unset this is a no-op. */
  _authToken() {
    try {
      return sessionStorage.getItem('vt_auth_token') || '';
    } catch (_) {
      return '';
    }
  }

  /**
   * Attach the access token to every /api/ request from one place, rather than
   * threading it through all 24 fetch call sites.
   */
  _installAuthFetch() {
    const original = window.fetch.bind(window);
    const readToken = () => this._authToken();
    window.fetch = (input, init = {}) => {
      const url = typeof input === 'string' ? input : (input?.url || '');
      const token = readToken();
      if (token && url.includes('/api/')) {
        const headers = new Headers(init.headers || (typeof input === 'object' ? input.headers : undefined));
        headers.set('X-API-Token', token);
        init = { ...init, headers };
      }
      return original(input, init);
    };
  }

  /** EventSource cannot set headers, so the SSE route also accepts ?token=. */
  _withAuthQuery(url) {
    const token = this._authToken();
    if (!token) return url;
    return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
  }

  /* ── Settings persistence ─────────────────────────────── */
  _stripCredentialFields(settings) {
    if (!settings || typeof settings !== 'object') return settings;
    const cleaned = JSON.parse(JSON.stringify(settings));
    delete cleaned.apiKey;
    delete cleaned.douyinCookie;
    delete cleaned.bilibiliCookie;
    if (cleaned.ai) delete cleaned.ai.apiKey;
    if (cleaned.sources) {
      delete cleaned.sources.douyinCookie;
      delete cleaned.sources.bilibiliCookie;
    }
    if (cleaned.tts) {
      delete cleaned.tts.elevenLabsApiKey;
      delete cleaned.tts.fptApiKey;
    }
    return cleaned;
  }

  _sessionCredentials() {
    try {
      return JSON.parse(sessionStorage.getItem('vt_session_credentials') || '{}');
    } catch (_) {
      return {};
    }
  }

  _saveSessionCredentials(overrides = {}) {
    const current = this._sessionCredentials();
    const next = {
      ...current,
      apiKey: this.apiKeyInput?.value || '',
      douyinCookie: this.douyinCookie?.value || '',
      bilibiliCookie: this.bilibiliCookie?.value || '',
      elevenLabsApiKey: this.elevenLabsApiKey?.value || '',
      fptApiKey: this.fptApiKey?.value || '',
      ...overrides,
    };
    try { sessionStorage.setItem('vt_session_credentials', JSON.stringify(next)); } catch (_) {}
    // Kept under its own key so _authToken() can read it before _initElements runs.
    try { sessionStorage.setItem('vt_auth_token', this.accessTokenInput?.value || ''); } catch (_) {}
  }

  _migratePersistentCredentials() {
    const migrated = this._sessionCredentials();
    const absorb = (settings) => {
      if (!settings || typeof settings !== 'object') return;
      migrated.apiKey ||= settings.apiKey || settings.ai?.apiKey || '';
      migrated.douyinCookie ||= settings.douyinCookie || settings.sources?.douyinCookie || '';
      migrated.bilibiliCookie ||= settings.bilibiliCookie || settings.sources?.bilibiliCookie || '';
      migrated.elevenLabsApiKey ||= settings.tts?.elevenLabsApiKey || '';
      migrated.fptApiKey ||= settings.tts?.fptApiKey || '';
    };
    try {
      const keys = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key === 'vt_settings' || key?.startsWith('vt_task_settings_')) keys.push(key);
      }
      keys.forEach((key) => {
        try {
          const settings = JSON.parse(localStorage.getItem(key) || '{}');
          absorb(settings);
          localStorage.setItem(key, JSON.stringify(this._stripCredentialFields(settings)));
        } catch (_) {
          localStorage.removeItem(key);
        }
      });
      sessionStorage.setItem('vt_session_credentials', JSON.stringify(migrated));
    } catch (_) {}
  }

  _applySessionCredentials() {
    const credentials = this._sessionCredentials();
    this._setIfPresent(this.accessTokenInput, this._authToken());
    this._setIfPresent(this.apiKeyInput, credentials.apiKey);
    this._setIfPresent(this.douyinCookie, credentials.douyinCookie);
    this._setIfPresent(this.bilibiliCookie, credentials.bilibiliCookie);
    this._setIfPresent(this.elevenLabsApiKey, credentials.elevenLabsApiKey);
    this._setIfPresent(this.fptApiKey, credentials.fptApiKey);
  }

  _saveSettings() {
    const s = {
      baseUrl:  this.modelBaseUrl.value,
      model:    this.modelSelect.value,
      summaryLang: this.summaryLangSel.value,
      ocr: this._getOcrSettings(),
      ocrDefaultVersion: 2,
    };
    try { localStorage.setItem('vt_settings', JSON.stringify(s)); } catch (_) {}
    this._saveSessionCredentials();
    this._saveActiveTaskSettings();
  }

  _loadSettings() {
    try {
      this._migratePersistentCredentials();
      const raw = localStorage.getItem('vt_settings');
      const s = raw ? JSON.parse(raw) : {};
      if (s.baseUrl) this.modelBaseUrl.value = s.baseUrl;
      if (s.ocr && s.ocrDefaultVersion !== 2 && Number.parseFloat(s.ocr.cropTop) === 60) {
        s.ocr.cropTop = 0;
      }
      if (s.ocr) this._applyOcrSettings(s.ocr);
      if (s.summaryLang) this.summaryLangSel.value = s.summaryLang;
      this._loadTaskSettingsDefaults(s);
      // Model options will be restored after fetching
      this._savedModel = s.model || '';
      this._applySessionCredentials();

      if (s.baseUrl && this.apiKeyInput?.value) {
        setTimeout(() => this._fetchModels(true), 400);
      }
    } catch (_) {}
  }

  _taskSettingsKey(taskId = this.currentTaskId) {
    return taskId ? `vt_task_settings_${taskId}` : '';
  }

  _getTaskScopedSettings() {
    const tts = this._getTtsSettings();
    delete tts.elevenLabsApiKey;
    delete tts.fptApiKey;
    return {
      ai: {
        baseUrl: this.modelBaseUrl?.value || '',
        model: this.modelSelect?.value || '',
        summaryLanguage: this.summaryLangSel?.value || 'vi',
      },
      sources: {},
      ocr: this._getOcrSettings(),
      tts,
      subtitle: this._getSubtitleSettings(),
      meta: {
        version: 2,
        subtitleMergeDefaultVersion: 2,
        savedAt: new Date().toISOString(),
      },
    };
  }

  _loadTaskSettingsDefaults(globalSettings = {}) {
    if (globalSettings.tts) this._applyTtsSettings(globalSettings.tts);
    if (globalSettings.subtitle && globalSettings.subtitleMergeDefaultVersion !== 2 && Number.parseFloat(globalSettings.subtitle.mergeSeconds) === 3) {
      globalSettings.subtitle.mergeSeconds = 0;
    }
    if (globalSettings.subtitle) {
      this._applySubtitleSettings(globalSettings.subtitle);
      this._hasSavedSubtitleSettings = true;
    }
  }

  async _loadActiveTaskSettings() {
    const key = this._taskSettingsKey();
    if (!key) return;
    try {
      let settings = null;
      try {
        const resp = await fetch(`${this.apiBase}/task-settings/${encodeURIComponent(this.currentTaskId)}`);
        if (resp.ok) {
          const payload = await resp.json();
          if (payload?.settings && Object.keys(payload.settings).length) settings = payload.settings;
        }
      } catch (_) {}
      const raw = localStorage.getItem(key);
      if (!settings && raw) settings = this._stripCredentialFields(JSON.parse(raw));
      if (!settings) {
        const globalSettings = JSON.parse(localStorage.getItem('vt_settings') || '{}');
        this._loadTaskSettingsDefaults(globalSettings);
        return;
      }
      if (settings.ai) {
        this._setIfPresent(this.modelBaseUrl, settings.ai.baseUrl);
        this._setIfPresent(this.summaryLangSel, settings.ai.summaryLanguage);
        this._savedModel = settings.ai.model || this._savedModel || '';
        if (settings.ai.baseUrl && this.apiKeyInput?.value) setTimeout(() => this._fetchModels(true), 100);
      }
      if (settings.ocr) this._applyOcrSettings(settings.ocr);
      if (settings.tts) this._applyTtsSettings(settings.tts);
      const mergeDefaultVersion = settings.meta?.subtitleMergeDefaultVersion ?? settings.subtitleMergeDefaultVersion;
      if (settings.subtitle && mergeDefaultVersion !== 2 && Number.parseFloat(settings.subtitle.mergeSeconds) === 3) {
        settings.subtitle.mergeSeconds = 0;
      }
      if (settings.subtitle) {
        this._applySubtitleSettings(settings.subtitle);
        this._hasSavedSubtitleSettings = true;
      }
      try { localStorage.setItem(key, JSON.stringify(this._stripCredentialFields(settings))); } catch (_) {}
    } catch (e) {
      console.warn('load task settings failed', e);
    }
  }

  _saveActiveTaskSettings() {
    const key = this._taskSettingsKey();
    if (!key) return;
    const settings = this._stripCredentialFields(this._getTaskScopedSettings());
    try { localStorage.setItem(key, JSON.stringify(settings)); } catch (_) {}
    clearTimeout(this.settingsSaveTimer);
    this.settingsSaveTimer = setTimeout(() => this._persistActiveTaskSettings(settings), 300);
  }

  async _persistActiveTaskSettings(settings = this._getTaskScopedSettings()) {
    if (!this.currentTaskId) return;
    try {
      await fetch(`${this.apiBase}/task-settings/${encodeURIComponent(this.currentTaskId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
    } catch (e) {
      console.warn('save task settings failed', e);
    }
  }

  async _persistCookieFileAsText(fileInput, targetTextarea) {
    const file = fileInput?.files?.[0];
    if (!file || !targetTextarea) return;
    try {
      const text = await file.text();
      const cookieText = this._cookieTextFromFileContent(text);
      if (cookieText) {
        targetTextarea.value = cookieText;
        this._saveSettings();
      }
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
    } finally {
      if (fileInput) fileInput.value = '';
    }
  }

  _cookieTextFromFileContent(text) {
    const raw = String(text || '').trim();
    if (!raw) return '';

    try {
      const parsed = JSON.parse(raw);
      const cookies = Array.isArray(parsed) ? parsed : (Array.isArray(parsed.cookies) ? parsed.cookies : []);
      const pairs = cookies
        .map(c => {
          if (!c || typeof c !== 'object') return '';
          const name = String(c.name || '').trim();
          if (!name) return '';
          const value = c.value === undefined || c.value === null ? '' : String(c.value);
          return `${name}=${value}`;
        })
        .filter(Boolean);
      if (pairs.length) return pairs.join('; ');
    } catch (_) {}

    const netscapePairs = raw.split(/\r?\n/)
      .map(line => line.trim())
      .filter(line => line && !line.startsWith('#'))
      .map(line => {
        const parts = line.split('\t');
        if (parts.length < 7) return '';
        const name = parts[5]?.trim();
        if (!name) return '';
        return `${name}=${parts.slice(6).join('\t')}`;
      })
      .filter(Boolean);
    if (netscapePairs.length) return netscapePairs.join('; ');

    const cookieHeader = raw.split(/\r?\n/).find(line => /^cookie\s*:/i.test(line));
    if (cookieHeader) return cookieHeader.replace(/^cookie\s*:\s*/i, '').trim();
    return raw.replace(/^cookie\s*:\s*/i, '').replace(/\r?\n/g, '; ').trim();
  }

  _loadLocalRecentTasks() {
    try {
      const raw = localStorage.getItem('vt_recent_tasks');
      const items = raw ? JSON.parse(raw) : [];
      return Array.isArray(items) ? items : [];
    } catch (_) {
      return [];
    }
  }

  _saveLocalRecentTasks(items) {
    try {
      localStorage.setItem('vt_recent_tasks', JSON.stringify(items.slice(0, 30)));
    } catch (_) {}
  }

  _mergeRecentTasks(serverItems, localItems) {
    const byId = new Map();
    [...localItems, ...serverItems].forEach(item => {
      if (!item?.task_id) return;
      byId.set(item.task_id, { ...(byId.get(item.task_id) || {}), ...item });
    });
    return [...byId.values()]
      .sort((a, b) => Number(b.completed_at || 0) - Number(a.completed_at || 0))
      .slice(0, 30);
  }

  _rememberRecentTask(taskId, task) {
    if (!taskId || !task) return;
    const existing = this.taskItems.get(taskId) || this._loadLocalRecentTasks().find(item => item.task_id === taskId) || {};
    const item = {
      task_id: taskId,
      status: task.status || existing.status || 'processing',
      progress: task.progress ?? existing.progress ?? 0,
      message: task.message || task.error || existing.message || '',
      video_title: task.custom_title || task.video_title || task.safe_title || existing.video_title || 'Untitled',
      original_video_title: task.original_video_title || task.video_title || task.safe_title || existing.original_video_title || 'Untitled',
      custom_title: task.custom_title || existing.custom_title || '',
      url: task.url || existing.url || this.videoUrlInput?.value?.trim() || '',
      detected_language: task.detected_language || existing.detected_language || '',
      summary_language: task.summary_language || existing.summary_language || this.summaryLangSel?.value || '',
      has_translation: Boolean(task.translation) || Boolean(existing.has_translation),
      has_media: Boolean(task.media_url || task.media_filename) || Boolean(existing.has_media),
      has_dubbed_video: Boolean(task.dubbed_video_url || task.dubbed_video_filename) || Boolean(existing.has_dubbed_video),
      dub_status: task.dub_status || existing.dub_status || '',
      dub_progress: task.dub_progress ?? existing.dub_progress ?? 0,
      dub_message: task.dub_message || existing.dub_message || '',
      dubbed_video_filename: task.dubbed_video_filename || existing.dubbed_video_filename || '',
      dubbed_video_url: task.dubbed_video_url || existing.dubbed_video_url || '',
      completed_at: task.completed_at || existing.completed_at || Math.floor(Date.now() / 1000),
      created_at: task.created_at || task.completed_at || existing.created_at || Math.floor(Date.now() / 1000),
    };
    const merged = this._mergeRecentTasks([item], this._loadLocalRecentTasks());
    this._saveLocalRecentTasks(merged);
    this.taskItems.set(taskId, item);
    this._renderRecentTasks(merged);
  }

  async _loadRecentTasks() {
    if (!this.recentPanel || !this.recentList) return;
    const localItems = this._loadLocalRecentTasks();
    try {
      const resp = await fetch(`${this.apiBase}/tasks/recent?limit=20`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const merged = this._mergeRecentTasks(data.items || [], localItems);
      this._saveLocalRecentTasks(merged);
      this._renderRecentTasks(merged);
      merged.forEach(item => {
        this.taskItems.set(item.task_id, item);
        if (item.status === 'processing') this._startSSE(item.task_id);
      });
      const savedActive = localStorage.getItem('vt_active_task_id');
      const active = merged.find(item => item.task_id === savedActive) || merged[0];
      if (active && !this.currentTaskId) this._openRecentTask(active.task_id);
    } catch (e) {
      console.warn('Recent tasks load failed:', e);
      this._renderRecentTasks(localItems);
      localItems.forEach(item => {
        this.taskItems.set(item.task_id, item);
        if (item.status === 'processing') this._startSSE(item.task_id);
      });
      const savedActive = localStorage.getItem('vt_active_task_id');
      const active = localItems.find(item => item.task_id === savedActive) || localItems[0];
      if (active && !this.currentTaskId) this._openRecentTask(active.task_id);
    }
  }

  _renderRecentTasks(items) {
    if (!this.recentPanel || !this.recentList) return;
    this.recentPanel.classList.add('show');
    this.recentList.innerHTML = '';
    items.forEach(item => {
      const row = document.createElement('div');
      row.className = `recent-item${item.task_id === this.currentTaskId ? ' active' : ''}`;
      row.dataset.taskId = item.task_id;
      row.tabIndex = 0;
      row.setAttribute('role', 'button');

      const title = document.createElement('div');
      title.className = 'recent-item-title';
      title.textContent = item.video_title || 'Untitled';

      const actions = document.createElement('div');
      actions.className = 'recent-actions';
      const renameBtn = document.createElement('button');
      renameBtn.type = 'button';
      renameBtn.className = 'recent-action-btn';
      renameBtn.title = this.t('rename_task');
      renameBtn.innerHTML = '<i class="fas fa-pen"></i>';
      renameBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this._renameRecentTask(item.task_id);
      });
      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'recent-action-btn danger';
      deleteBtn.title = this.t('delete_task');
      deleteBtn.innerHTML = '<i class="fas fa-trash-alt"></i>';
      deleteBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this._deleteRecentTask(item.task_id);
      });
      actions.appendChild(renameBtn);
      actions.appendChild(deleteBtn);

      const titleRow = document.createElement('div');
      titleRow.className = 'recent-title-row';
      titleRow.appendChild(title);
      titleRow.appendChild(actions);

      const meta = document.createElement('div');
      meta.className = 'recent-item-meta';
      const flags = [];
      if (item.has_media) flags.push(this.t('media_ready'));
      if (item.has_translation) flags.push(this.t('translation_ready'));
      const timeText = this._formatRecentTime(item.completed_at);
      meta.textContent = [timeText, flags.join(' / '), item.url || ''].filter(Boolean).join(' · ');
      const status = document.createElement('div');
      status.className = `recent-status ${item.status || ''}`;
      status.textContent = item.status === 'processing'
        ? `${Math.round(Number(item.progress || 0))}%`
        : (item.status || 'queued');

      row.appendChild(status);
      row.appendChild(titleRow);
      row.appendChild(meta);
      row.addEventListener('click', () => this._openRecentTask(item.task_id));
      row.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          this._openRecentTask(item.task_id);
        }
      });
      this.recentList.appendChild(row);
    });
  }

  _replaceLocalRecentTask(taskId, patch) {
    const items = this._loadLocalRecentTasks().map(item => (
      item.task_id === taskId ? { ...item, ...patch } : item
    ));
    this._saveLocalRecentTasks(items);
    if (this.taskItems.has(taskId)) {
      this.taskItems.set(taskId, { ...this.taskItems.get(taskId), ...patch });
    }
    this._renderRecentTasks(items);
  }

  _removeLocalRecentTask(taskId) {
    const items = this._loadLocalRecentTasks().filter(item => item.task_id !== taskId);
    this._saveLocalRecentTasks(items);
    this.taskItems.delete(taskId);
    localStorage.removeItem(`vt_task_settings_${taskId}`);
    localStorage.removeItem(`vt_voice_overrides_${taskId}`);
    this._renderRecentTasks(items);
    return items;
  }

  async _renameRecentTask(taskId) {
    const item = this.taskItems.get(taskId) || this._loadLocalRecentTasks().find(x => x.task_id === taskId);
    const current = item?.custom_title || item?.video_title || '';
    const next = window.prompt(this.t('rename_task_prompt'), current);
    if (next === null) return;
    const title = next.trim();
    try {
      const fd = new FormData();
      fd.append('title', title);
      const resp = await fetch(`${this.apiBase}/task/${encodeURIComponent(taskId)}/title`, { method: 'PATCH', body: fd });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      this._replaceLocalRecentTask(taskId, {
        custom_title: data.custom_title || '',
        video_title: data.video_title || item?.original_video_title || 'Untitled',
      });
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
    }
  }

  async _deleteRecentTask(taskId) {
    if (!window.confirm(this.t('delete_task_confirm'))) return;
    try {
      const resp = await fetch(`${this.apiBase}/task/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      this._stopSSE(taskId);
      const remaining = this._removeLocalRecentTask(taskId);
      if (this.currentTaskId === taskId) {
        this.currentTaskId = null;
        localStorage.removeItem('vt_active_task_id');
        this._hideProgress();
        this._hideResults();
        this.emptyState.style.display = 'grid';
        if (remaining[0]) this._openRecentTask(remaining[0].task_id);
      }
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
    }
  }

  _formatRecentTime(value) {
    const ts = Number(value || 0);
    if (!ts) return '';
    try {
      return new Date(ts * 1000).toLocaleString();
    } catch (_) {
      return '';
    }
  }

  async _openRecentTask(taskId) {
    if (!taskId) return;
    try {
      const resp = await fetch(`${this.apiBase}/task-status/${encodeURIComponent(taskId)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const task = await resp.json();
      this.currentTaskId = taskId;
      localStorage.setItem('vt_active_task_id', taskId);
      await this._loadActiveTaskSettings();
      this._rememberRecentTask(taskId, task);
      this._hideError();
      this._setLoading(false);
      if (task.status === 'completed') {
        this._hideProgress();
        this._showResults(
          task.script,
          task.summary,
          task.video_title,
          task.translation,
          task.detected_language,
          task.summary_language,
          task.media_url,
          task.media_filename,
          task.timed_transcript,
          task.timed_translation,
        );
        this._restoreDubProgress(task);
      } else if (task.status === 'processing') {
        this._hideResults();
        this._showProgress();
        this._renderProgress(task.progress || 0, task.message || this.t('processing'));
        this._renderTaskLog(this._taskLogsFromUpdate(taskId, task));
        if (this.resumeTaskBtn) this.resumeTaskBtn.style.display = 'inline-flex';
        this._startSSE(taskId);
      } else if (task.status === 'error') {
        this._hideProgress();
        this._hideResults();
        this._showError(task.error || task.message || 'Processing error', { canResume: true });
      }
      this._renderRecentTasks(this._loadLocalRecentTasks());
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
    }
  }

  _switchSettingsTab(name) {
    const target = name || 'ai';
    this.settingsTabBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.settingsTab === target);
    });
    this.settingsPanes.forEach(pane => {
      pane.classList.toggle('active', pane.id === `settingsPane${target.charAt(0).toUpperCase()}${target.slice(1)}`);
    });
    if (!this.settingsBody.classList.contains('open')) this._openSettings();
    this._updateSubtitlePreview();
  }

  _openSettings() {
    this.settingsBody.classList.add('open');
    this.settingsToggle.classList.add('open');
    document.body.classList.add('settings-modal-open');
  }

  _closeSettings() {
    this.settingsBody.classList.remove('open');
    this.settingsToggle.classList.remove('open');
    document.body.classList.remove('settings-modal-open');
  }

  _setIfPresent(el, value) {
    if (el && value !== undefined && value !== null && value !== '') el.value = value;
  }

  _setSelectOptions(selectEl, items, selectedValue) {
    if (!selectEl) return;
    const normalized = this._normalizeChoiceItems(items);
    const selected = String(selectedValue || selectEl.value || normalized[0]?.id || '').trim();
    selectEl.innerHTML = '';
    normalized.forEach(item => {
      const option = document.createElement('option');
      option.value = item.id;
      option.textContent = item.name;
      option.dataset.search = item.search || item.name.toLowerCase();
      option.dataset.country = item.countryKey || '';
      selectEl.appendChild(option);
    });
    if (selected && !normalized.some(item => item.id === selected)) {
      const option = document.createElement('option');
      option.value = selected;
      option.textContent = selected;
      selectEl.appendChild(option);
    }
    if (selected) selectEl.value = selected;
  }

  _normalizeChoiceItems(items) {
    return (items || [])
      .map(item => {
        if (typeof item === 'string') {
          const id = item.trim();
          return { id, name: id, search: id.toLowerCase(), countryKey: '' };
        }
        const id = String(item?.id || item?.value || item?.voice_id || item?.name || '').trim();
        const label = String(item?.name || item?.label || item?.display_name || id).trim();
        const language = String(item?.language || item?.locale || item?.lang || '').trim();
        const country = String(item?.country || item?.country_code || item?.region || item?.accent || '').trim();
        const category = String(item?.category || item?.provider || '').trim();
        const detail = [language, country, category].filter(Boolean).join(' · ');
        const name = detail ? `${label} — ${detail}` : label;
        const countryKey = (country || language || '').toLowerCase();
        const search = [id, label, language, country, category].join(' ').toLowerCase();
        return { id, name, search, countryKey, countryLabel: country || language || 'Unknown' };
      })
      .filter(item => item.id);
  }

  _applySubtitleSettings(settings) {
    this._setIfPresent(this.subtitleMode, settings.mode);
    this._setIfPresent(this.subtitleSource, settings.source);
    this._setIfPresent(this.secondarySubtitleSource, settings.secondarySource);
    this._setIfPresent(this.subtitleFontSize, settings.fontSize);
    this._setIfPresent(this.subtitlePosition, settings.position);
    this._setIfPresent(this.subtitleXOffset, settings.xOffset);
    this._setIfPresent(this.subtitleYOffset, settings.yOffset);
    this._setIfPresent(this.subtitleMergeSeconds, settings.mergeSeconds);
    this._setIfPresent(this.subtitleTimeOffset, settings.timeOffset);
    this._setIfPresent(this.subtitleTextColor, settings.textColor);
    this._setIfPresent(this.subtitleBoxColor, settings.boxColor);
    this._setIfPresent(this.subtitleOutlineColor, settings.outlineColor);
    this._setIfPresent(this.secondarySubtitleFontSize, settings.secondaryFontSize);
    this._setIfPresent(this.secondarySubtitleTextColor, settings.secondaryTextColor);
    this._setIfPresent(this.secondarySubtitleBoxColor, settings.secondaryBoxColor);
    this._setIfPresent(this.secondarySubtitleOutlineColor, settings.secondaryOutlineColor);
    this._updateSubtitlePreview();
  }

  _applyOcrSettings(settings) {
    if (!settings) return;
    this._setIfPresent(this.ocrLanguage, settings.language);
    this._setIfPresent(this.ocrFps, settings.fps);
    this._setIfPresent(this.ocrCropTop, settings.cropTop);
    this._setIfPresent(this.ocrCropBottom, settings.cropBottom);
    this._setIfPresent(this.ocrCropLeft, settings.cropLeft);
    this._setIfPresent(this.ocrCropRight, settings.cropRight);
    this._setIfPresent(this.ocrConfidence, settings.confidence);
  }

  _getOcrSettings() {
    const num = (el, fallback) => {
      const value = Number.parseFloat(el?.value || '');
      return Number.isFinite(value) ? value : fallback;
    };
    return {
      language: this.ocrLanguage?.value || 'eng+vie+chi_sim',
      fps: num(this.ocrFps, 2),
      cropTop: num(this.ocrCropTop, 0),
      cropBottom: num(this.ocrCropBottom, 0),
      cropLeft: num(this.ocrCropLeft, 0),
      cropRight: num(this.ocrCropRight, 0),
      confidence: num(this.ocrConfidence, 45),
    };
  }

  _appendOcrSettings(fd) {
    const ocr = this._getOcrSettings();
    fd.append('ocr_language', ocr.language);
    fd.append('ocr_fps', String(ocr.fps));
    fd.append('ocr_crop_top', String(ocr.cropTop));
    fd.append('ocr_crop_bottom', String(ocr.cropBottom));
    fd.append('ocr_crop_left', String(ocr.cropLeft));
    fd.append('ocr_crop_right', String(ocr.cropRight));
    fd.append('ocr_confidence', String(ocr.confidence));
  }

  _appendTaskSettings(fd) {
    fd.append('settings_json', JSON.stringify(this._getTaskScopedSettings()));
  }

  _getTtsSettings() {
    const rawTimingScale = Number.parseFloat(this.dubTimingScale?.value || '1');
    const timingScale = Number.isFinite(rawTimingScale) && rawTimingScale >= 0.8 && rawTimingScale <= 1.2
      ? rawTimingScale
      : 1;
    return {
      speakerMode: this.ttsSpeakerMode?.value || 'auto_gender',
      provider: this.ttsProvider?.value || 'vieneu',
      style: this.ttsStyle?.value || 'tu_nhien',
      elevenLabsApiKey: this.elevenLabsApiKey?.value || '',
      elevenLabsModelId: this.elevenLabsModelId?.value || 'eleven_multilingual_v2',
      elevenLabsDefaultVoiceId: this.elevenLabsDefaultVoiceId?.value || '',
      elevenLabsMaleVoiceId: this.elevenLabsMaleVoiceId?.value || '',
      elevenLabsFemaleVoiceId: this.elevenLabsFemaleVoiceId?.value || '',
      fptApiKey: this.fptApiKey?.value || '',
      fptSpeed: this.fptSpeed?.value || '0',
      fptDefaultVoice: this.fptDefaultVoice?.value || 'banmai',
      fptMaleVoice: this.fptMaleVoice?.value || 'leminh',
      fptFemaleVoice: this.fptFemaleVoice?.value || 'banmai',
      audioMode: this.dubAudioMode?.value || 'replace',
      originalVolume: this.dubOriginalVolume?.value || '25',
      includeSubtitles: this.dubIncludeSubtitles?.value || 'off',
      mergeSeconds: this.ttsMergeSeconds?.value || '0',
      maxFitSpeed: this.ttsMaxFitSpeed?.value || '1.3',
      timingScale: String(timingScale),
      audioOffset: this.dubAudioOffset?.value || '0',
      defaultVoice: this.ttsDefaultVoice?.value || 'Phạm Tuyên',
      maleVoice: this.ttsMaleVoice?.value || 'Phạm Tuyên',
      femaleVoice: this.ttsFemaleVoice?.value || 'Trúc Ly',
    };
  }

  _applyTtsSettings(settings) {
    const speakerMode = settings.speakerMode === 'default' ? 'auto_gender' : settings.speakerMode;
    const provider = ['vieneu', 'elevenlabs', 'fpt'].includes(settings.provider) ? settings.provider : 'vieneu';
    this._setIfPresent(this.ttsSpeakerMode, speakerMode);
    this._setIfPresent(this.ttsProvider, provider);
    this._setIfPresent(this.ttsStyle, settings.style);
    this._setIfPresent(this.elevenLabsApiKey, settings.elevenLabsApiKey);
    this._setIfPresent(this.elevenLabsModelId, settings.elevenLabsModelId);
    this._setIfPresent(this.elevenLabsDefaultVoiceId, settings.elevenLabsDefaultVoiceId);
    this._setIfPresent(this.elevenLabsMaleVoiceId, settings.elevenLabsMaleVoiceId);
    this._setIfPresent(this.elevenLabsFemaleVoiceId, settings.elevenLabsFemaleVoiceId);
    this._setIfPresent(this.fptApiKey, settings.fptApiKey);
    this._setIfPresent(this.fptSpeed, settings.fptSpeed);
    this._setIfPresent(this.fptDefaultVoice, settings.fptDefaultVoice);
    this._setIfPresent(this.fptMaleVoice, settings.fptMaleVoice);
    this._setIfPresent(this.fptFemaleVoice, settings.fptFemaleVoice);
    this._setIfPresent(this.dubAudioMode, settings.audioMode);
    this._setIfPresent(this.dubOriginalVolume, settings.originalVolume);
    this._setIfPresent(this.dubIncludeSubtitles, settings.includeSubtitles);
    this._setIfPresent(this.ttsMergeSeconds, settings.mergeSeconds);
    this._setIfPresent(this.ttsMaxFitSpeed, settings.maxFitSpeed);
    this._setIfPresent(this.dubTimingScale, settings.timingScale);
    this._setIfPresent(this.dubAudioOffset, settings.audioOffset);
    this._setIfPresent(this.ttsDefaultVoice, settings.defaultVoice);
    this._setIfPresent(this.ttsMaleVoice, settings.maleVoice);
    this._setIfPresent(this.ttsFemaleVoice, settings.femaleVoice);
    this._updateTtsProviderFields();
    this._updateCloneUsageLabels();
    if (provider === 'elevenlabs' && this.elevenLabsApiKey?.value) this._loadElevenLabsVoices({
      defaultVoice: settings.elevenLabsDefaultVoiceId,
      maleVoice: settings.elevenLabsMaleVoiceId,
      femaleVoice: settings.elevenLabsFemaleVoiceId,
    });
  }

  _updateTtsProviderFields() {
    let provider = this.ttsProvider?.value || 'vieneu';
    if (!['vieneu', 'elevenlabs', 'fpt'].includes(provider)) {
      provider = 'vieneu';
      if (this.ttsProvider) this.ttsProvider.value = 'vieneu';
    }
    this.ttsProviderFields?.forEach(field => {
      field.classList.toggle('is-hidden', field.dataset.ttsProviderField !== provider);
    });
    this._updateCloneUsageLabels();
    if (provider === 'elevenlabs' && this.elevenLabsApiKey?.value) this._loadElevenLabsVoices();
  }

  _cloneInputForRole(role) {
    if (role === 'male') return this.ttsMaleClone;
    if (role === 'female') return this.ttsFemaleClone;
    return this.ttsDefaultClone;
  }

  _cloneStatusForRole(role) {
    if (role === 'male') return this.ttsMaleCloneStatus;
    if (role === 'female') return this.ttsFemaleCloneStatus;
    return this.ttsDefaultCloneStatus;
  }

  _updateCloneUsageLabels(activeRole = null, activeText = '') {
    [
      ['default', this.ttsDefaultVoice],
      ['male', this.ttsMaleVoice],
      ['female', this.ttsFemaleVoice],
    ].forEach(([role, selectEl]) => {
      const statusEl = this._cloneStatusForRole(role);
      if (!statusEl) return;
      const file = this._cloneInputForRole(role)?.files?.[0];
      const voice = selectEl?.value || 'selected voice';
      if (activeRole === role && activeText) {
        statusEl.innerHTML = activeText;
      } else if (file) {
        statusEl.innerHTML = `Preview/export: <strong>clone</strong> ${this._escapeHtml(file.name)} + base voice ${this._escapeHtml(voice)}`;
      } else {
        statusEl.innerHTML = `Preview/export: <strong>built-in</strong> ${this._escapeHtml(voice)} only`;
      }
    });
  }

  _escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[char]));
  }

  async _loadElevenLabsVoices(selected = {}) {
    const apiKey = this.elevenLabsApiKey?.value?.trim() || '';
    if (!apiKey) return;
    const trigger = this.fetchElevenLabsVoicesBtn;
    const previousHtml = trigger?.innerHTML;
    if (trigger) {
      trigger.disabled = true;
      trigger.innerHTML = '<span class="spinner"></span>';
    }
    try {
      const fd = new FormData();
      fd.append('api_key', apiKey);
      const resp = await fetch(`${this.apiBase}/elevenlabs/voices`, { method: 'POST', body: fd });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = typeof payload.detail === 'string' ? payload.detail : `HTTP ${resp.status}`;
        throw new Error(detail);
      }
      const voices = this._normalizeChoiceItems(payload.data || []);
      const defaultSelected = selected.defaultVoice || this.elevenLabsDefaultVoiceId?.value || voices[0]?.id || '';
      this._setSelectOptions(this.elevenLabsDefaultVoiceId, voices, defaultSelected);
      this._setSelectOptions(this.elevenLabsMaleVoiceId, voices, selected.maleVoice || this.elevenLabsMaleVoiceId?.value || defaultSelected);
      this._setSelectOptions(this.elevenLabsFemaleVoiceId, voices, selected.femaleVoice || this.elevenLabsFemaleVoiceId?.value || defaultSelected);
      this._saveSettings();
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
    } finally {
      if (trigger) {
        trigger.disabled = false;
        trigger.innerHTML = previousHtml || '<i class="fas fa-sync-alt"></i>';
      }
    }
  }

  /* ── Fetch models ─────────────────────────────────────── */
  _isLikelyModelBaseUrl(value) {
    try {
      const u = new URL(value);
      const host = u.hostname.toLowerCase();
      const videoHosts = ['tiktok.com', 'douyin.com', 'youtube.com', 'youtu.be', 'bilibili.com', 'facebook.com', 'instagram.com', 'soundcloud.com'];
      return (u.protocol === 'http:' || u.protocol === 'https:') && !videoHosts.some(h => host.includes(h));
    } catch (_) {
      return false;
    }
  }

  async _fetchModels(silent = false) {
    const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
    const apiKey  = this.apiKeyInput.value.trim();

    if (!baseUrl || !apiKey) {
      if (!silent) this._setFetchStatus('err', this.t('api_key') + ' & URL required');
      return;
    }
    if (!this._isLikelyModelBaseUrl(baseUrl)) {
      if (!silent) this._setFetchStatus('err', this.t('error_model_base_url'));
      return;
    }

    this.fetchModelsBtn.disabled = true;
    this.fetchIcon.className = 'fas fa-spinner fa-spin';
    if (!silent) this._setFetchStatus('', this.t('fetching_models'));

    try {
      const fd = new FormData();
      fd.append('base_url', baseUrl);
      fd.append('api_key',  apiKey);

      const resp = await fetch(`${this.apiBase}/models`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const models = data.data || data.models || [];

      // Rebuild select options
      this.modelSelect.innerHTML = `<option value="">${this.t('model_default')}</option>`;
      models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name || m.id;
        this.modelSelect.appendChild(opt);
      });

      // Restore previously selected model
      if (this._savedModel) {
        this.modelSelect.value = this._savedModel;
        this._savedModel = '';
      }

      this._setFetchStatus('ok', typeof this.t('models_loaded') === 'function'
        ? this.t('models_loaded')(models.length)
        : `${models.length} models`);

    } catch (e) {
      console.warn('Model fetch error:', e);
      this._setFetchStatus('err', this.t('models_error') + ': ' + e.message);
    } finally {
      this.fetchModelsBtn.disabled = false;
      this.fetchIcon.className = 'fas fa-sync-alt';
    }
  }

  _setFetchStatus(cls, msg) {
    this.fetchStatus.className = 'fetch-status' + (cls ? ` ${cls}` : '');
    this.fetchStatus.textContent = msg;
  }

  /* ── Transcription ────────────────────────────────────── */
  async _startTranscription() {
    if (this.submitBtn.disabled) return;

    const url     = this.videoUrlInput.value.trim();
    const sumLang = this.summaryLangSel.value;
    const downloadOnly = Boolean(this.downloadOnly?.checked);
    const hardSubtitleOcr = Boolean(this.hardSubtitleOcr?.checked);

    if (!url) { this._showError(this.t('error_invalid_url')); return; }
    const douyinCookie = this.douyinCookie?.value.trim() || '';
    if (/douyin\.com|v\.douyin\.com|iesdouyin\.com|amemv\.com/i.test(url) && !douyinCookie) {
      this._showError(this.t('error_douyin_cookie_required'));
      this._switchSettingsTab('sources');
      return;
    }

    this._setLoading(true);
    this._hideError();
    this._showProgress();

    try {
      const fd = new FormData();
      fd.append('url',              url);
      fd.append('summary_language', sumLang);
      fd.append('download_only',    downloadOnly ? 'true' : 'false');
      fd.append('hard_subtitle_ocr', hardSubtitleOcr ? 'true' : 'false');
      this._appendOcrSettings(fd);
      this._appendTaskSettings(fd);

      const apiKey  = this.apiKeyInput.value.trim();
      const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
      const modelId = this.modelSelect.value;
      if (apiKey)  fd.append('api_key',       apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id',       modelId);
      const bilibiliCookie = this.bilibiliCookie?.value.trim() || '';
      if (douyinCookie) fd.append('douyin_cookie', douyinCookie);
      if (bilibiliCookie) fd.append('bilibili_cookie', bilibiliCookie);

      const resp = await fetch(`${this.apiBase}/process-video`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Request failed');
      }

      const data = await resp.json();
      this.currentTaskId = data.task_id;
      localStorage.setItem('vt_active_task_id', data.task_id);
      this._rememberRecentTask(data.task_id, {
        status: 'processing',
        progress: 5,
        message: downloadOnly || hardSubtitleOcr ? this.t('downloading_video') : this.t('preparing'),
        video_title: url,
        url,
        summary_language: sumLang,
        download_only: downloadOnly,
        hard_subtitle_ocr: hardSubtitleOcr,
        created_at: Math.floor(Date.now() / 1000),
      });

      this._initSP();
      this._updateProgress(5, downloadOnly || hardSubtitleOcr ? this.t('downloading_video') : this.t('preparing'), true);
      this._startSSE(data.task_id);
      this._saveSettings();
      await this._persistActiveTaskSettings();
      this._setLoading(false);

    } catch (err) {
      this._showError(this.t('error_processing_failed') + err.message);
      this._setLoading(false);
      this._hideProgress();
    }
  }

  async _startFileUpload(file) {
    if (this.submitBtn.disabled) return;

    const parts = (file.name || '').split('.');
    const ext = parts.length > 1 ? ('.' + parts.pop().toLowerCase()) : '';
    if (!this._allowedUploadExts.has(ext)) {
      this._showError(this.t('error_upload_type'));
      return;
    }
    if (!file.size) {
      this._showError(this.t('error_upload_empty'));
      return;
    }
    const maxB = this.uploadMaxMb * 1024 * 1024;
    if (file.size > maxB) {
      this._showError(this.t('error_upload_size')(this.uploadMaxMb));
      return;
    }

    this._setLoading(true);
    this._hideError();
    this._showProgress();

    const sumLang = this.summaryLangSel.value;
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      fd.append('summary_language', sumLang);
      const hardSubtitleOcr = Boolean(this.hardSubtitleOcr?.checked);
      fd.append('hard_subtitle_ocr', hardSubtitleOcr ? 'true' : 'false');
      this._appendOcrSettings(fd);
      this._appendTaskSettings(fd);

      const apiKey  = this.apiKeyInput.value.trim();
      const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
      const modelId = this.modelSelect.value;
      if (apiKey)  fd.append('api_key',       apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id',       modelId);

      const resp = await fetch(`${this.apiBase}/process-video`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        const d = err.detail;
        const msg = typeof d === 'string'
          ? d
          : (Array.isArray(d) && d[0] && (d[0].msg || d[0].message))
            || `HTTP ${resp.status}`;
        throw new Error(msg);
      }

      const data = await resp.json();
      this.currentTaskId = data.task_id;
      localStorage.setItem('vt_active_task_id', data.task_id);
      this._rememberRecentTask(data.task_id, {
        status: 'processing',
        progress: 5,
        message: this.t('preparing'),
        video_title: file.name,
        summary_language: sumLang,
        hard_subtitle_ocr: hardSubtitleOcr,
        created_at: Math.floor(Date.now() / 1000),
      });

      this._initSP();
      this._updateProgress(5, this.t('preparing'), true);
      this._startSSE(data.task_id);
      this._saveSettings();
      await this._persistActiveTaskSettings();
      this._setLoading(false);

    } catch (err) {
      this._showError(this.t('error_processing_failed') + err.message);
      this._setLoading(false);
      this._hideProgress();
    }
  }

  /* ── SSE ──────────────────────────────────────────────── */
  _startSSE(taskId = this.currentTaskId) {
    if (!taskId) return;
    if (this.taskStreams.has(taskId)) {
      const existing = this.taskStreams.get(taskId);
      if (taskId === this.currentTaskId) this.eventSource = existing;
      return;
    }
    const stream = new EventSource(this._withAuthQuery(`${this.apiBase}/task-stream/${taskId}`));
    this.taskStreams.set(taskId, stream);
    if (taskId === this.currentTaskId) this.eventSource = stream;

    stream.onmessage = (ev) => {
      try {
        const task = JSON.parse(ev.data);
        if (task.type === 'heartbeat') return;

        this._rememberRecentTask(taskId, task);
        const isActive = taskId === this.currentTaskId;
        if (isActive) {
          this._updateProgress(task.progress, task.message, true);
          this._renderTaskLog(this._taskLogsFromUpdate(taskId, task));
        }

        if (task.status === 'completed') {
          if (isActive) {
            this._stopSP(); this._setLoading(false); this._hideProgress();
            this._showResults(task.script, task.summary, task.video_title, task.translation, task.detected_language, task.summary_language, task.media_url, task.media_filename, task.timed_transcript, task.timed_translation);
          }
          this._stopSSE(taskId);
          this._loadRecentTasks();
        } else if (task.status === 'error') {
          if (isActive) {
            this._stopSP(); this._setLoading(false); this._hideProgress();
            this._showError(task.error || 'Processing error', { canResume: true });
          }
          this._stopSSE(taskId);
        }
      } catch (_) {}
    };

    stream.onerror = async () => {
      this._stopSSE(taskId);
      try {
        if (taskId) {
          const r = await fetch(`${this.apiBase}/task-status/${taskId}`);
          if (r.ok) {
            const task = await r.json();
            this._rememberRecentTask(taskId, task);
            if (task?.status === 'completed') {
              if (taskId === this.currentTaskId) {
                this._stopSP(); this._setLoading(false); this._hideProgress();
                this._showResults(task.script, task.summary, task.video_title, task.translation, task.detected_language, task.summary_language, task.media_url, task.media_filename, task.timed_transcript, task.timed_translation);
              }
              this._loadRecentTasks();
              return;
            }
          }
        }
      } catch (_) {}
      if (taskId === this.currentTaskId) {
        this._showError(this.t('error_processing_failed') + 'SSE disconnected');
        this._setLoading(false);
      }
    };
  }

  _stopSSE(taskId = null) {
    if (taskId) {
      const stream = this.taskStreams.get(taskId);
      if (stream) stream.close();
      this.taskStreams.delete(taskId);
      if (this.eventSource === stream) this.eventSource = null;
      return;
    }
    this.taskStreams.forEach(stream => stream.close());
    this.taskStreams.clear();
    if (this.eventSource) { this.eventSource.close(); this.eventSource = null; }
  }

  /* ── Progress ─────────────────────────────────────────── */
  _updateProgress(pct, msg, fromServer = false) {
    if (fromServer) {
      this._stopSP();
      this.sp.lastServer = pct;
      this.sp.current    = pct;
      this._renderProgress(pct, msg);
      this._updateStage(pct, msg);
      this._startSP();
    } else {
      this._renderProgress(pct, msg);
    }
  }

  _updateStage(pct, msg) {
    const m = (msg || '').toLowerCase();

    // ── 字幕路径（快速）──────────────────────────────────────
    if (m.includes('获取成功') || m.includes('subtitle found') || m.includes('字幕获取')) {
      this.sp.stage = 'subtitle_found';
      this.sp.target = 55;
      this._setModeBadge('subtitle');
    }
    // ── 无字幕 → 音频下载路径（慢）────────────────────────────
    else if (m.includes('未找到字幕') || m.includes('no subtitle') || m.includes('下载视频音频') || m.includes('下载音频')) {
      this.sp.stage = 'downloading';
      this.sp.target = 55;
      this._setModeBadge('whisper');
    }
    else if (m.includes('读取文本') || (m.includes('read') && m.includes('text'))) {
      this.sp.stage = 'parsing';
      this.sp.target = 55;
      this._setModeBadge('whisper');
    }
    else if (m.includes('转换音频') || m.includes('准备转录')) {
      this.sp.stage = 'downloading';
      this.sp.target = 55;
      this._setModeBadge('whisper');
    }
    else if (m.includes('上传') || m.includes('upload')) {
      this.sp.stage = 'preparing';
      this.sp.target = 40;
    }
    // ── 通用字幕检测中 ─────────────────────────────────────────
    else if (m.includes('检测') && (m.includes('字幕') || m.includes('subtitle'))) {
      this.sp.stage = 'subtitle';
      this.sp.target = 40;
    }
    // ── 其他阶段 ───────────────────────────────────────────────
    else if (m.includes('解析') || m.includes('pars'))                     { this.sp.stage = 'parsing';       this.sp.target = 60; }
    else if (m.includes('下载') || m.includes('download'))                 { this.sp.stage = 'downloading';   this.sp.target = 60; }
    else if (m.includes('转录') || m.includes('transcrib') || m.includes('whisper')) { this.sp.stage = 'transcribing';  this.sp.target = 80; }
    else if (m.includes('优化') || m.includes('optimiz'))                  { this.sp.stage = 'optimizing';    this.sp.target = 90; }
    else if (m.includes('摘要') || m.includes('summary'))                  { this.sp.stage = 'summarizing';   this.sp.target = 95; }
    else if (m.includes('完成') || m.includes('complet'))                  { this.sp.stage = 'completed';     this.sp.target = 100; }

    if (pct >= this.sp.target) this.sp.target = Math.min(pct + 8, 99);
  }

  _setModeBadge(mode) {
    if (!this.modeBadge) return;
    if (mode === 'subtitle') {
      this.modeBadge.textContent  = this.t('mode_subtitle');
      this.modeBadge.className    = 'mode-badge subtitle';
      this.modeBadge.style.display = 'inline-block';
      if (this.progressFill) this.progressFill.classList.add('subtitle-mode');
    } else if (mode === 'whisper') {
      this.modeBadge.textContent  = this.t('mode_whisper');
      this.modeBadge.className    = 'mode-badge whisper';
      this.modeBadge.style.display = 'inline-block';
      if (this.progressFill) this.progressFill.classList.remove('subtitle-mode');
    }
  }

  _initSP() {
    this.sp.enabled = false; this.sp.current = 0; this.sp.target = 15;
    this.sp.lastServer = 0;  this.sp.startTime = Date.now(); this.sp.stage = 'preparing';
  }
  _startSP() {
    if (this.sp.interval) clearInterval(this.sp.interval);
    this.sp.enabled   = true;
    this.sp.startTime = this.sp.startTime || Date.now();
    this.sp.interval  = setInterval(() => this._tickSP(), 500);
  }
  _stopSP() {
    if (this.sp.interval) { clearInterval(this.sp.interval); this.sp.interval = null; }
    this.sp.enabled = false;
  }
  _tickSP() {
    if (!this.sp.enabled || this.sp.current >= this.sp.target) return;
    const speeds = { subtitle: .5, parsing: .3, downloading: .18, transcribing: .14, optimizing: .22, summarizing: .28 };
    let inc = speeds[this.sp.stage] || .2;
    const remaining = this.sp.target - this.sp.current;
    if (remaining < 5) inc *= .3;
    const next = Math.min(this.sp.current + inc, this.sp.target);
    if (next > this.sp.current) {
      this.sp.current = next;
      this._renderProgress(next, this._stageMsg());
    }
  }
  _stageMsg() {
    const map = {
      subtitle:       this.t('detecting_subtitles'),
      subtitle_found: this.t('subtitle_found'),
      downloading:    this.t('downloading_video'),
      parsing:        this.t('parsing_video'),
      transcribing:   this.t('transcribing_audio'),
      optimizing:     this.t('optimizing_transcript'),
      summarizing:    this.t('generating_summary'),
      completed:      this.t('completed'),
    };
    return map[this.sp.stage] || this.t('processing');
  }

  _renderProgress(pct, msg) {
    const p = Math.round(pct * 10) / 10;
    this.progressStatus.textContent = `${p}%`;
    this.progressFill.style.width   = `${p}%`;

    // Translate common server messages — more specific checks first
    const m = (msg || '').toLowerCase();
    let label = msg;
    // ── Subtitle path ──────────────────────────────────────────
    if      (m.includes('获取成功') || m.includes('subtitle found'))        label = this.t('subtitle_found');
    else if (m.includes('未找到字幕') || m.includes('no subtitle'))         label = this.t('no_subtitle');
    else if (m.includes('检测') && (m.includes('字幕') || m.includes('subtitle'))) label = this.t('detecting_subtitles');
    // ── Audio / Whisper path ────────────────────────────────────
    else if (m.includes('下载') || m.includes('download'))  label = this.t('downloading_video');
    else if (m.includes('解析') || m.includes('pars'))      label = this.t('parsing_video');
    else if (m.includes('转录') || m.includes('transcrib')) label = this.t('transcribing_audio');
    else if (m.includes('优化') || m.includes('optimiz'))   label = this.t('optimizing_transcript');
    else if (m.includes('摘要') || m.includes('summary'))   label = this.t('generating_summary');
    else if (m.includes('完成') || m.includes('complet'))   label = this.t('completed');
    else if (m.includes('准备') || m.includes('prepar'))    label = this.t('preparing');

    this.progressMessage.textContent = label;
  }

  _renderTaskLog(logs = []) {
    if (!this.taskLogBody) return;
    const entries = Array.isArray(logs) ? logs.slice(-40) : [];
    this.taskLogBody.innerHTML = entries.map(entry => {
      const ts = Number(entry?.ts || 0);
      const time = ts ? new Date(ts * 1000).toLocaleTimeString([], { hour12: false }) : '--:--:--';
      const progress = Number.isFinite(Number(entry?.progress)) ? `${Math.round(Number(entry.progress))}%` : '';
      const message = this._escapeHtml(String(entry?.message || ''));
      return `<div class="task-log-line"><span class="time">${time}</span><span class="pct">${progress}</span><span class="text">${message}</span></div>`;
    }).join('');
    if (this.taskLogCount) this.taskLogCount.textContent = String(entries.length);
    this.taskLogBody.scrollTop = this.taskLogBody.scrollHeight;
  }

  _taskLogsFromUpdate(taskId, task) {
    if (Array.isArray(task?.task_logs) && task.task_logs.length) {
      this.liveTaskLogs.set(taskId, task.task_logs.slice(-80));
      return task.task_logs;
    }
    const message = task?.message || task?.error || '';
    if (!taskId || !message) return this.liveTaskLogs.get(taskId) || [];
    const logs = this.liveTaskLogs.get(taskId) || [];
    const entry = {
      ts: Math.floor(Date.now() / 1000),
      status: task.status || '',
      progress: Math.max(0, Math.min(100, Number(task.progress || 0))),
      message,
    };
    const previous = logs[logs.length - 1] || {};
    if (
      previous.message !== entry.message
      || Math.round(Number(previous.progress || 0)) !== Math.round(entry.progress)
      || previous.status !== entry.status
    ) {
      logs.push(entry);
      this.liveTaskLogs.set(taskId, logs.slice(-80));
    }
    return this.liveTaskLogs.get(taskId) || [];
  }

  _showProgress() {
    this.emptyState.style.display    = 'none';
    this.resultsPanel.classList.remove('show');
    this.progressPanel.classList.add('show');
    this._renderTaskLog([]);
    if (this.resumeTaskBtn) this.resumeTaskBtn.style.display = this.currentTaskId ? 'inline-flex' : 'none';
    this._stopDubProgressPolling();
    if (this.dubProgressPanel) this.dubProgressPanel.classList.remove('show', 'error', 'done');
    // Reset mode badge & progress bar color for new task
    if (this.modeBadge) { this.modeBadge.style.display = 'none'; this.modeBadge.className = 'mode-badge'; }
    if (this.progressFill) this.progressFill.classList.remove('subtitle-mode');
  }
  _hideProgress() {
    this.progressPanel.classList.remove('show');
    if (this.resumeTaskBtn) this.resumeTaskBtn.style.display = 'none';
  }

  /* ── Results ──────────────────────────────────────────── */
  /** 与后端 Translator.normalize_lang_code 对齐，用于 Tab 展示判断 */
  _normLangTab(code) {
    if (!code) return '';
    const c = String(code).toLowerCase().trim();
    if (c.startsWith('zh')) return 'zh';
    if (c.length >= 2) return c.slice(0, 2);
    return c;
  }

  _renderMarkdown(value) {
    const source = String(value || '');
    const fallback = () => this._escapeHtml(source).replace(/\r?\n/g, '<br>');
    if (!window.marked?.parse || !window.DOMPurify?.sanitize) return fallback();
    try {
      const rendered = window.marked.parse(source, { gfm: true, breaks: true });
      return window.DOMPurify.sanitize(rendered, {
        USE_PROFILES: { html: true },
        FORBID_TAGS: ['style', 'script', 'iframe', 'object', 'embed', 'form'],
        FORBID_ATTR: ['style'],
      });
    } catch (_) {
      return fallback();
    }
  }

  _showResults(script, summary, videoTitle, translation, detectedLang, summaryLang, mediaUrl, mediaFilename, timedTranscript, timedTranslation) {
    this.emptyState.style.display = 'none';
    this.progressPanel.classList.remove('show');
    this.scriptContent.innerHTML  = script    ? this._renderMarkdown(script)      : '';
    this.summaryContent.innerHTML = summary   ? this._renderMarkdown(summary)     : '';
    this.currentTimedTranscript = timedTranscript || script || '';
    this.currentTimedTranslation = timedTranslation || translation || '';

    const d = this._normLangTab(detectedLang);
    const s = this._normLangTab(summaryLang);
    const showTranslation = Boolean(translation) && d && s && d !== s;
    if (showTranslation) {
      this.translationContent.innerHTML = this._renderMarkdown(translation);
      this.translationTabBtn.style.display  = 'inline-block';
      this.dlTranslation.style.display      = 'inline-flex';
    } else {
      this.translationTabBtn.style.display  = 'none';
      this.dlTranslation.style.display      = 'none';
    }
    if (this.regenerateTranslationBtn) {
      this.regenerateTranslationBtn.style.display = (this.currentTaskId && d && s && d !== s) ? 'inline-flex' : 'none';
    }
    if (this.regenerateTranscriptBtn) {
      const hasTranscriptText = Boolean(script || timedTranscript);
      this.regenerateTranscriptBtn.style.display = (this.currentTaskId && hasTranscriptText) ? 'inline-flex' : 'none';
    }
    this._loadVoiceOverrides();

    const cues = this._getSelectedSubtitleCues();
    const hasMedia = Boolean(mediaUrl || mediaFilename);
    const hasCues = cues.length > 0;
    this.reviewTabBtn.style.display = hasMedia ? 'inline-block' : 'none';
    if (this.runHardSubtitleOcrBtn) {
      this.runHardSubtitleOcrBtn.style.display = (this.currentTaskId && hasMedia) ? 'inline-flex' : 'none';
    }
    this.exportVideoBtn.style.display = (hasMedia && hasCues) ? 'inline-flex' : 'none';
    this.exportDubbedVideoBtn.style.display = (hasMedia && hasCues) ? 'inline-flex' : 'none';
    if (hasMedia) {
      this._renderReview(mediaUrl, mediaFilename, videoTitle, cues);
    } else {
      this._clearReview();
    }

    this.resultsPanel.classList.add('show');
    this._switchTab(hasMedia ? 'review' : 'script');
    this.resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  _restoreDubProgress(task) {
    if (!task) return;
    this.currentDubbedVideoUrl = task.dubbed_video_url || '';
    this.currentDubbedVideoFilename = task.dubbed_video_filename || '';
    if (task.dub_status) {
      this._renderDubProgress(task);
      if (task.dub_status === 'processing') {
        this._startDubProgressPolling(task.task_id || this.currentTaskId);
      } else {
        this._stopDubProgressPolling();
      }
    } else if (this.dubProgressPanel) {
      this.dubProgressPanel.classList.remove('show', 'error', 'done');
    }
    this._updateDubbedExportButton();
  }

  _updateDubbedExportButton() {
    if (!this.exportDubbedVideoBtn || this.exportDubbedVideoBtn.disabled) return;
    if (this.currentDubbedVideoUrl || this.currentDubbedVideoFilename) {
      this.exportDubbedVideoBtn.innerHTML = `<i class="fas fa-download"></i> <span>${this.t('download_dubbed_video') || 'Download Dubbed Video'}</span>`;
    } else {
      this.exportDubbedVideoBtn.innerHTML = `<i class="fas fa-microphone"></i> <span>${this.t('export_dubbed_video')}</span>`;
    }
  }

  _downloadDubbedVideoFromTask() {
    const filename = this.currentDubbedVideoFilename || 'dubbed_video.mp4';
    const url = this.currentDubbedVideoUrl || `${this.apiBase}/media/${encodeURIComponent(filename)}`;
    const a = document.createElement('a');
    a.href = this._withAuthQuery(url);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  _hideResults() {
    this.resultsPanel.classList.remove('show');
    if (this.scriptContent) this.scriptContent.innerHTML = '';
    if (this.summaryContent) this.summaryContent.innerHTML = '';
    if (this.translationContent) this.translationContent.innerHTML = '';
    this.currentDubbedVideoUrl = '';
    this.currentDubbedVideoFilename = '';
    this._updateDubbedExportButton();
    this._clearReview();
  }

  _parseTimedTranscript(markdown) {
    const text = String(markdown || '').replace(/\r\n/g, '\n');
    const lines = text.split('\n');
    const cues = [];
    // Minutes allow 3 digits: older tasks stored "100:00" style timestamps.
    const timestamp = String.raw`\d{1,3}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?`;
    const timeLine = new RegExp(String.raw`^\s*(?:\*\*)?\[(${timestamp})\s*-\s*(${timestamp})\](?:\*\*)?\s*$`);

    for (let i = 0; i < lines.length; i++) {
      const match = lines[i].match(timeLine);
      if (!match) continue;

      const body = [];
      for (let j = i + 1; j < lines.length; j++) {
        if (lines[j].match(timeLine)) break;
        if (/^\s*#{1,6}\s+/.test(lines[j])) break;
        body.push(lines[j]);
      }

      const cueText = body.join('\n')
        .replace(/\*\*/g, '')
        .replace(/\n{2,}/g, '\n')
        .trim();

      if (cueText) {
        cues.push({
          start: this._timeToSeconds(match[1]),
          end: this._timeToSeconds(match[2]),
          label: `${match[1]} - ${match[2]}`,
          text: cueText,
        });
      }
    }

    return cues.filter(c => Number.isFinite(c.start) && Number.isFinite(c.end));
  }

  _timeToSeconds(value) {
    const parts = String(value || '').replace(',', '.').split(':');
    const tail = Number.parseFloat(parts[parts.length - 1] || '');
    const head = parts.slice(0, -1).map(n => Number.parseInt(n, 10));
    if (!Number.isFinite(tail) || head.some(Number.isNaN)) return NaN;
    if (parts.length === 2) return head[0] * 60 + tail;
    if (parts.length === 3) return head[0] * 3600 + head[1] * 60 + tail;
    return NaN;
  }

  _getSubtitleSettings() {
    return {
      mode: this.subtitleMode?.value || 'single',
      source: this.subtitleSource?.value || 'translation',
      secondarySource: this.secondarySubtitleSource?.value || 'transcript',
      fontSize: Number.parseInt(this.subtitleFontSize?.value || '28', 10),
      position: this.subtitlePosition?.value || 'bottom',
      xOffset: Number.parseInt(this.subtitleXOffset?.value || '0', 10),
      yOffset: Number.parseInt(this.subtitleYOffset?.value || '0', 10),
      mergeSeconds: Number.parseFloat(this.subtitleMergeSeconds?.value || '0'),
      timeOffset: Number.parseFloat(this.subtitleTimeOffset?.value || '0'),
      textColor: this.subtitleTextColor?.value || '#ffffff',
      boxColor: this.subtitleBoxColor?.value || '#000000',
      outlineColor: this.subtitleOutlineColor?.value || '#000000',
      secondaryFontSize: Number.parseInt(this.secondarySubtitleFontSize?.value || '22', 10),
      secondaryTextColor: this.secondarySubtitleTextColor?.value || '#ffffff',
      secondaryBoxColor: this.secondarySubtitleBoxColor?.value || '#000000',
      secondaryOutlineColor: this.secondarySubtitleOutlineColor?.value || '#000000',
    };
  }

  _getSubtitleCuesForSource(source) {
    const settings = this._getSubtitleSettings();
    const requested = source || 'translation';
    const translationCues = this._parseTimedTranscript(this.currentTimedTranslation || '');
    const cues = requested === 'translation' && translationCues.length
      ? translationCues
      : this._parseTimedTranscript(this.currentTimedTranscript || '');
    return this._offsetSubtitleCues(this._mergeShortSubtitleCues(cues, settings.mergeSeconds), settings.timeOffset);
  }

  _offsetSubtitleCues(cues, offsetSeconds = 0) {
    const offset = Number.parseFloat(offsetSeconds || 0);
    if (!Number.isFinite(offset) || Math.abs(offset) < 0.001) return cues;
    return cues.map(cue => {
      const start = Math.max(0, Number(cue.start || 0) + offset);
      const end = Math.max(start + 0.05, Number(cue.end || start) + offset);
      return { ...cue, start, end };
    });
  }

  _getDubCuesWithOriginalTiming() {
    const originalCues = this._parseTimedTranscript(this.currentTimedTranscript || '');
    const translationCues = this._parseTimedTranscript(this.currentTimedTranslation || '');
    if (!originalCues.length) return translationCues;
    if (!translationCues.length) return originalCues;
    return originalCues.map((cue, index) => ({
      start: cue.start,
      end: cue.end,
      text: (translationCues[index]?.text || cue.text || '').trim(),
    })).filter(cue => cue.text);
  }

  _mergeShortSubtitleCues(cues, minDuration = 3) {
    const threshold = Math.max(0, Math.min(30, Number.parseFloat(minDuration || 0)));
    if (!threshold || cues.length < 2) return cues;
    const merged = [];
    let index = 0;
    while (index < cues.length) {
      const cue = cues[index];
      const start = Number(cue.start || 0);
      let end = Number(cue.end || start);
      const textParts = [String(cue.text || '').trim()].filter(Boolean);
      if (end - start >= threshold) {
        merged.push({ start, end, text: textParts.join('\n') });
        index += 1;
        continue;
      }
      index += 1;
      while (index < cues.length && end - start < threshold) {
        const nextCue = cues[index];
        end = Math.max(end, Number(nextCue.end || end));
        const nextText = String(nextCue.text || '').trim();
        if (nextText) textParts.push(nextText);
        index += 1;
      }
      merged.push({ start, end, text: textParts.join('\n') });
    }
    return merged;
  }

  _getSelectedSubtitleCues() {
    return this._getSubtitleCuesForSource(this.subtitleSource?.value || 'translation');
  }

  _voiceOverrideStorageKey() {
    return `vt_voice_overrides_${this.currentTaskId || 'none'}`;
  }

  _cueVoiceKey(cue) {
    const start = Number(cue?.start || 0).toFixed(3);
    const end = Number(cue?.end || 0).toFixed(3);
    return `${start}|${end}`;
  }

  _loadVoiceOverrides() {
    try {
      const raw = localStorage.getItem(this._voiceOverrideStorageKey());
      const parsed = raw ? JSON.parse(raw) : {};
      this.voiceOverrides = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch (_) {
      this.voiceOverrides = {};
    }
  }

  _saveVoiceOverrides() {
    try {
      localStorage.setItem(this._voiceOverrideStorageKey(), JSON.stringify(this.voiceOverrides || {}));
    } catch (_) {}
  }

  _voiceOverridesForExport() {
    const overrides = this.voiceOverrides || {};
    return this.currentReviewCues
      .map(cue => {
        const voice = overrides[this._cueVoiceKey(cue)];
        if (!['male', 'female', 'default'].includes(voice)) return null;
        return { start: cue.start, end: cue.end, voice };
      })
      .filter(Boolean);
  }

  _activeSubtitleText(currentTime, source) {
    const cues = this._getSubtitleCuesForSource(source);
    for (const cue of cues) {
      if (currentTime >= cue.start && currentTime < cue.end) return cue.text;
    }
    return '';
  }

  _updateSubtitlePreview() {
    if (!this.subtitlePreview || !this.subtitlePreviewText) return;
    const settings = this._getSubtitleSettings();
    const previewScale = this._subtitlePreviewScale();
    if (this.secondarySubtitleSettings) {
      this.secondarySubtitleSettings.classList.toggle('show', settings.mode === 'dual');
    }
    if (this.reviewMediaEl && this.currentSubtitleSourceForRows !== settings.source) {
      this._refreshCueRows(this.reviewMediaEl);
    }
    const currentTime = this.reviewMediaEl ? this.reviewMediaEl.currentTime : 0;
    const primaryText = this._activeSubtitleText(currentTime, settings.source);
    const secondaryText = settings.mode === 'dual'
      ? this._activeSubtitleText(currentTime, settings.secondarySource)
      : '';

    this.subtitlePreview.classList.toggle('show', Boolean(primaryText || secondaryText));
    this._styleSubtitlePreviewLine(
      this.subtitlePreviewText,
      primaryText,
      settings.fontSize,
      settings.textColor,
      settings.boxColor,
      settings.outlineColor,
      previewScale.font,
    );
    if (this.secondarySubtitlePreviewText) {
      this._styleSubtitlePreviewLine(
        this.secondarySubtitlePreviewText,
        secondaryText,
        settings.secondaryFontSize,
        settings.secondaryTextColor,
        settings.secondaryBoxColor,
        settings.secondaryOutlineColor,
        previewScale.font,
      );
      this.secondarySubtitlePreviewText.style.display = settings.mode === 'dual' && secondaryText ? '' : 'none';
    }
    this.subtitlePreview.style.top = '';
    this.subtitlePreview.style.bottom = '';
    const xOffset = Number.isFinite(settings.xOffset) ? settings.xOffset * previewScale.x : 0;
    const yOffset = Number.isFinite(settings.yOffset) ? settings.yOffset * previewScale.y : 0;
    this.subtitlePreview.style.left = `calc(50% + ${xOffset}px)`;
    this.subtitlePreview.style.right = 'auto';
    this.subtitlePreview.style.transform = 'translateX(-50%)';
    if (settings.position === 'top') this.subtitlePreview.style.top = `calc(12% + ${yOffset}px)`;
    else if (settings.position === 'middle') this.subtitlePreview.style.top = `calc(50% + ${yOffset}px)`;
    else this.subtitlePreview.style.bottom = `calc(12% - ${yOffset}px)`;
  }

  _subtitlePreviewScale() {
    const media = this.reviewMediaEl;
    if (!media || media.tagName !== 'VIDEO') return { x: 1, y: 1, font: 1 };
    const rect = media.getBoundingClientRect();
    const naturalWidth = media.videoWidth || rect.width || 1280;
    const naturalHeight = media.videoHeight || rect.height || 720;
    const x = rect.width && naturalWidth ? rect.width / naturalWidth : 1;
    const y = rect.height && naturalHeight ? rect.height / naturalHeight : x;
    return { x, y, font: y || x || 1 };
  }

  _styleSubtitlePreviewLine(el, text, fontSize, textColor, boxColor, outlineColor, scale = 1) {
    if (!el) return;
    el.textContent = text || '';
    el.style.display = text ? '' : 'none';
    const renderedSize = Math.max(14, Math.min(72, fontSize || 28));
    const previewSize = Math.max(10, renderedSize * (Number.isFinite(scale) ? scale : 1));
    el.style.fontSize = `${previewSize}px`;
    el.style.color = textColor;
    el.style.backgroundColor = this._hexToRgba(boxColor, 0.55);
    el.style.textShadow = `0 1px 2px ${outlineColor}, 0 0 3px ${outlineColor}`;
  }

  _hexToRgba(hex, alpha) {
    const v = String(hex || '#000000').replace('#', '');
    if (!/^[0-9a-fA-F]{6}$/.test(v)) return `rgba(0,0,0,${alpha})`;
    const r = Number.parseInt(v.slice(0, 2), 16);
    const g = Number.parseInt(v.slice(2, 4), 16);
    const b = Number.parseInt(v.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  _renderReview(mediaUrl, mediaFilename, videoTitle, cues) {
    const src = mediaUrl || `${this.apiBase}/media/${encodeURIComponent(mediaFilename)}`;
    const ext = (mediaFilename || mediaUrl || '').split('?')[0].split('.').pop().toLowerCase();
    const isVideo = ['mp4', 'webm', 'mkv'].includes(ext);

    this.reviewTitle.textContent = videoTitle || mediaFilename || '';
    this.reviewMedia.innerHTML = '';
    this.cueList.innerHTML = '';

    const media = document.createElement(isVideo ? 'video' : 'audio');
    media.controls = true;
    media.preload = 'metadata';
    media.src = this._withAuthQuery(src);
    this.reviewMedia.appendChild(media);
    this.reviewMediaEl = media;

    this.subtitlePreview = document.createElement('div');
    this.subtitlePreview.className = 'subtitle-preview';
    this.subtitlePreviewText = document.createElement('div');
    this.subtitlePreviewText.className = 'subtitle-preview-text';
    this.secondarySubtitlePreviewText = document.createElement('div');
    this.secondarySubtitlePreviewText.className = 'subtitle-preview-text';
    this.subtitlePreview.appendChild(this.subtitlePreviewText);
    this.subtitlePreview.appendChild(this.secondarySubtitlePreviewText);
    this.reviewMedia.appendChild(this.subtitlePreview);

    const translationCues = this._parseTimedTranscript(this.currentTimedTranslation || '');
    if (this.subtitleSource && !this._hasSavedSubtitleSettings) {
      this.subtitleSource.value = translationCues.length ? 'translation' : 'transcript';
    }

    this.currentReviewCues = cues;
    this.currentSubtitleSourceForRows = null;
    this._refreshCueRows(media);

    media.addEventListener('timeupdate', () => {
      this._syncCue(media.currentTime, this.currentReviewCues);
      this._updateSubtitlePreview();
    });
    media.addEventListener('loadedmetadata', () => this._updateSubtitlePreview());
    if (this._reviewResizeHandler) {
      window.removeEventListener('resize', this._reviewResizeHandler);
    }
    this._reviewResizeHandler = () => this._updateSubtitlePreview();
    window.addEventListener('resize', this._reviewResizeHandler);
    this._syncCue(0, this.currentReviewCues);
    this._updateSubtitlePreview();
  }

  _refreshCueRows(media) {
    if (!this.cueList || !media) return;
    const settings = this._getSubtitleSettings();
    const cues = this._getSelectedSubtitleCues();
    this.currentReviewCues = cues;
    this.currentSubtitleSourceForRows = settings.source;
    this.cueList.innerHTML = '';

    cues.forEach((cue, index) => {
      const row = document.createElement('div');
      row.className = 'cue-row';
      row.dataset.index = String(index);
      row.dataset.start = String(cue.start);

      const time = document.createElement('span');
      time.className = 'cue-time';
      time.textContent = cue.label;

      const body = document.createElement('textarea');
      body.className = 'cue-text-edit';
      body.value = cue.text;
      body.rows = Math.min(6, Math.max(2, String(cue.text || '').split('\n').length));
      body.addEventListener('click', (e) => e.stopPropagation());
      body.addEventListener('input', () => {
        cue.text = body.value;
        this.currentReviewCues[index] = { ...cue, text: body.value };
        this._replaceCueTextInCurrentSource(cue.start, cue.end, body.value);
        this._updateSubtitlePreview();
        this._queueCueTextSave(row, cue, body.value);
      });

      const voice = document.createElement('select');
      voice.className = 'cue-voice-select';
      voice.innerHTML = `
        <option value="">Auto</option>
        <option value="male">Male</option>
        <option value="female">Female</option>
      `;
      voice.value = this.voiceOverrides[this._cueVoiceKey(cue)] || '';
      voice.addEventListener('click', (e) => e.stopPropagation());
      voice.addEventListener('change', () => {
        const key = this._cueVoiceKey(cue);
        if (voice.value) this.voiceOverrides[key] = voice.value;
        else delete this.voiceOverrides[key];
        this._saveVoiceOverrides();
      });

      row.appendChild(time);
      row.appendChild(body);
      row.appendChild(voice);
      row.addEventListener('click', () => {
        media.currentTime = cue.start;
        media.play().catch(() => {});
      });
      this.cueList.appendChild(row);
    });

    if (!cues.length) {
      this.cueList.innerHTML = `<div class="review-empty">${this.t('no_timed_transcript')}</div>`;
    }
    this._syncCue(media.currentTime || 0, cues);
  }

  _replaceCueTextInCurrentSource(start, end, text) {
    const source = this.currentSubtitleSourceForRows || this.subtitleSource?.value || 'translation';
    const raw = source === 'translation' ? this.currentTimedTranslation : this.currentTimedTranscript;
    const cues = this._parseTimedTranscript(raw || '');
    const mid = Number(start || 0) + Math.max(0, Number(end || start) - Number(start || 0)) / 2;
    let changed = false;
    cues.forEach(cue => {
      const cueMid = Number(cue.start || 0) + Math.max(0, Number(cue.end || cue.start) - Number(cue.start || 0)) / 2;
      if (!changed && (Math.abs(Number(cue.start) - Number(start)) < 0.05 && Math.abs(Number(cue.end) - Number(end)) < 0.05 || (Number(cue.start) <= mid && mid <= Number(cue.end)))) {
        cue.text = text;
        changed = true;
      }
    });
    if (!changed) return;
    const markdown = cues.map(cue => `**[${this._secondsToDisplayTime(cue.start)} - ${this._secondsToDisplayTime(cue.end)}]**\n\n${cue.text || ''}`).join('\n\n');
    if (source === 'translation') this.currentTimedTranslation = markdown;
    else this.currentTimedTranscript = markdown;
  }

  _secondsToDisplayTime(seconds) {
    const whole = Math.max(0, Math.round(Number(seconds || 0)));
    const h = Math.floor(whole / 3600);
    const m = Math.floor((whole % 3600) / 60);
    const s = whole % 60;
    if (h) return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  _queueCueTextSave(row, cue, text) {
    if (!this.currentTaskId) return;
    clearTimeout(row._saveTimer);
    row.classList.remove('saved');
    row.classList.add('saving');
    row._saveTimer = setTimeout(() => this._saveCueText(row, cue, text), 650);
  }

  async _saveCueText(row, cue, text) {
    try {
      const fd = new FormData();
      fd.append('source', this.currentSubtitleSourceForRows || this.subtitleSource?.value || 'translation');
      fd.append('start', String(cue.start));
      fd.append('end', String(cue.end));
      fd.append('text', text);
      const resp = await fetch(`${this.apiBase}/update-cue-text/${this.currentTaskId}`, { method: 'POST', body: fd });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
      if (data.timed_translation) this.currentTimedTranslation = data.timed_translation;
      if (data.timed_transcript) this.currentTimedTranscript = data.timed_transcript;
      row.classList.remove('saving');
      row.classList.add('saved');
      setTimeout(() => row.classList.remove('saved'), 1200);
    } catch (e) {
      row.classList.remove('saving');
      this._showError(this.t('error_processing_failed') + e.message);
    }
  }

  _syncCue(currentTime, cues) {
    if (!this.cueList) return;
    let activeIndex = -1;
    for (let i = 0; i < cues.length; i++) {
      if (currentTime >= cues[i].start && currentTime < cues[i].end) {
        activeIndex = i;
        break;
      }
    }

    this.cueList.querySelectorAll('.cue-row').forEach(row => {
      const active = Number(row.dataset.index) === activeIndex;
      row.classList.toggle('active', active);
      if (active) row.scrollIntoView({ block: 'nearest' });
    });
  }

  _clearReview() {
    if (this.reviewTitle) this.reviewTitle.textContent = '';
    if (this.reviewMedia) this.reviewMedia.innerHTML = '';
    if (this.cueList) {
      this.cueList.innerHTML = `<div class="review-empty">${this.t('no_timed_transcript')}</div>`;
    }
    if (this._reviewResizeHandler) {
      window.removeEventListener('resize', this._reviewResizeHandler);
      this._reviewResizeHandler = null;
    }
    this.reviewMediaEl = null;
  }

  /* ── Tabs ─────────────────────────────────────────────── */
  _switchTab(name) {
    this.tabBtns.forEach(b  => b.classList.toggle('active',  b.dataset.tab === name));
    this.tabPanes.forEach(p => p.classList.toggle('active', p.id === `${name}Tab`));
  }

  /* ── Download ─────────────────────────────────────────── */
  async _downloadFile(type) {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }
    try {
      const r = await fetch(`${this.apiBase}/task-status/${this.currentTaskId}`);
      if (!r.ok) throw new Error('Failed to get task status');
      const task = await r.json();

      let filename;
      if      (type === 'script')      filename = task.script_path      ? task.script_path.split('/').pop()      : `transcript_${task.safe_title||'x'}_${task.short_id||'x'}.md`;
      else if (type === 'summary')     filename = task.summary_path     ? task.summary_path.split('/').pop()     : `summary_${task.safe_title||'x'}_${task.short_id||'x'}.md`;
      else if (type === 'translation') filename = task.translation_path ? task.translation_path.split('/').pop() : `translation_${task.safe_title||'x'}_${task.short_id||'x'}.md`;
      else throw new Error('Unknown type');

      const a = document.createElement('a');
      a.href = this._withAuthQuery(`${this.apiBase}/download/${encodeURIComponent(filename)}`);
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (e) {
      this._showError(this.t('error_download_failed') + e.message);
    }
  }

  async _exportVideo() {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }

    const originalHtml = this.exportVideoBtn.innerHTML;
    this.exportVideoBtn.disabled = true;
    this.exportVideoBtn.innerHTML = `<span class="spinner"></span> <span>${this.t('exporting_video')}</span>`;

    try {
      const settings = this._getSubtitleSettings();
      const fd = new FormData();
      fd.append('subtitle_mode', settings.mode);
      fd.append('subtitle_source', settings.source);
      fd.append('secondary_subtitle_source', settings.secondarySource);
      fd.append('font_size', String(settings.fontSize || 28));
      fd.append('text_color', settings.textColor);
      fd.append('outline_color', settings.outlineColor);
      fd.append('box_color', settings.boxColor);
      fd.append('secondary_font_size', String(settings.secondaryFontSize || 22));
      fd.append('secondary_text_color', settings.secondaryTextColor);
      fd.append('secondary_outline_color', settings.secondaryOutlineColor);
      fd.append('secondary_box_color', settings.secondaryBoxColor);
      fd.append('position', settings.position);
      fd.append('subtitle_x_offset', String(Number.isFinite(settings.xOffset) ? settings.xOffset : 0));
      fd.append('subtitle_y_offset', String(Number.isFinite(settings.yOffset) ? settings.yOffset : 0));
      fd.append('subtitle_merge_seconds', String(Number.isFinite(settings.mergeSeconds) ? settings.mergeSeconds : 0));
      fd.append('subtitle_time_offset', String(Number.isFinite(settings.timeOffset) ? settings.timeOffset : 0));

      const resp = await fetch(`${this.apiBase}/export-video/${this.currentTaskId}`, { method: 'POST', body: fd });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`;
        throw new Error(detail);
      }

      const filename = data.filename || 'translated_video.mp4';
      const url = data.url || `${this.apiBase}/media/${encodeURIComponent(filename)}`;
      const fileResp = await fetch(url);
      if (!fileResp.ok) throw new Error(`Download HTTP ${fileResp.status}`);
      const blob = await fileResp.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);
    } catch (e) {
      this._showError(this.t('error_export_failed') + e.message);
    } finally {
      this.exportVideoBtn.disabled = false;
      this.exportVideoBtn.innerHTML = originalHtml;
    }
  }

  async _exportSubtitleFile(format) {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }
    const settings = this._getSubtitleSettings();
    const mergeSeconds = Number.isFinite(settings.mergeSeconds) ? settings.mergeSeconds : 0;
    const timeOffset = Number.isFinite(settings.timeOffset) ? settings.timeOffset : 0;
    const url = `${this.apiBase}/export-subtitles/${this.currentTaskId}?source=${encodeURIComponent(settings.source)}&format=${encodeURIComponent(format)}&merge_seconds=${encodeURIComponent(mergeSeconds)}&time_offset=${encodeURIComponent(timeOffset)}`;
    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
      }
      const disposition = resp.headers.get('content-disposition') || '';
      const match = disposition.match(/filename="?([^"]+)"?/i);
      const filename = match ? match[1] : `${settings.source}.${format}`;
      const blob = await resp.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);
    } catch (e) {
      this._showError(this.t('error_download_failed') + e.message);
    }
  }

  _elevenLabsVoiceIdForRole(role) {
    if (role === 'male') return this.elevenLabsMaleVoiceId?.value || this.elevenLabsDefaultVoiceId?.value || '';
    if (role === 'female') return this.elevenLabsFemaleVoiceId?.value || this.elevenLabsDefaultVoiceId?.value || '';
    return this.elevenLabsDefaultVoiceId?.value || '';
  }

  _fptVoiceForRole(role) {
    if (role === 'male') return this.fptMaleVoice?.value || this.fptDefaultVoice?.value || 'leminh';
    if (role === 'female') return this.fptFemaleVoice?.value || this.fptDefaultVoice?.value || 'banmai';
    return this.fptDefaultVoice?.value || 'banmai';
  }

  async _previewVoice(selectEl, buttonEl, role = 'default') {
    if (!selectEl || !buttonEl) return;
    const originalHtml = buttonEl.innerHTML;
    buttonEl.disabled = true;
    buttonEl.innerHTML = '<span class="spinner"></span>';
    try {
      const fd = new FormData();
      const provider = this.ttsProvider?.value || 'vieneu';
      const voice = provider === 'elevenlabs'
        ? this._elevenLabsVoiceIdForRole(role)
        : (provider === 'fpt' ? this._fptVoiceForRole(role) : (selectEl.value || 'Phạm Tuyên'));
      if (provider === 'elevenlabs' && !voice) {
        throw new Error('Select or load an ElevenLabs voice first');
      }
      if (provider === 'fpt' && !this.fptApiKey?.value) {
        throw new Error('FPT API key is missing');
      }
      const cloneFile = provider === 'vieneu' ? this._cloneInputForRole(role)?.files?.[0] : null;
      if (provider === 'vieneu') {
        const cloneText = cloneFile
          ? `Previewing: <strong>clone</strong> ${this._escapeHtml(cloneFile.name)} + base voice ${this._escapeHtml(voice)}`
          : `Previewing: <strong>built-in</strong> ${this._escapeHtml(voice)} only`;
        this._updateCloneUsageLabels(role, cloneText);
      }
      fd.append('provider', provider);
      fd.append('voice', voice);
      fd.append('style', this.ttsStyle?.value || 'tu_nhien');
      fd.append('elevenlabs_api_key', this.elevenLabsApiKey?.value || '');
      fd.append('elevenlabs_model_id', this.elevenLabsModelId?.value || 'eleven_multilingual_v2');
      fd.append('fpt_api_key', this.fptApiKey?.value || '');
      fd.append('fpt_speed', this.fptSpeed?.value || '0');
      if (cloneFile) fd.append('clone_audio', cloneFile);
      fd.append('text', 'Xin chào, đây là câu đọc thử bằng tiếng Việt để kiểm tra giọng lồng tiếng cho video.');
      const resp = await fetch(`${this.apiBase}/tts-preview`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      const objectUrl = URL.createObjectURL(blob);
      const audio = new Audio(objectUrl);
      audio.addEventListener('ended', () => URL.revokeObjectURL(objectUrl), { once: true });
      audio.addEventListener('error', () => URL.revokeObjectURL(objectUrl), { once: true });
      await audio.play();
      if (provider === 'vieneu') this._updateCloneUsageLabels();
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
      if ((this.ttsProvider?.value || 'vieneu') === 'vieneu') this._updateCloneUsageLabels();
    } finally {
      buttonEl.disabled = false;
      buttonEl.innerHTML = originalHtml;
    }
  }

  async _regenerateTranslation() {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }
    if (!this.regenerateTranslationBtn) return;

    const originalHtml = this.regenerateTranslationBtn.innerHTML;
    this.regenerateTranslationBtn.disabled = true;
    this.regenerateTranslationBtn.innerHTML = `<span class="spinner"></span> <span>${this.t('regenerating_translation')}</span>`;
    this._renderDubProgress({ dub_status: 'processing', dub_progress: 0, dub_message: this.t('regenerating_translation') });
    this._startDubProgressPolling();

    try {
      const fd = new FormData();
      const sumLang = this.summaryLangSel?.value || 'vi';
      const apiKey = this.apiKeyInput?.value?.trim() || '';
      const baseUrl = this.modelBaseUrl?.value?.trim() || '';
      const modelId = this.modelSelect?.value || '';
      fd.append('summary_language', sumLang);
      if (apiKey) fd.append('api_key', apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id', modelId);

      const resp = await fetch(`${this.apiBase}/regenerate-translation/${this.currentTaskId}`, { method: 'POST', body: fd });
      const task = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = typeof task.detail === 'string' ? task.detail : `HTTP ${resp.status}`;
        throw new Error(detail);
      }

      this._rememberRecentTask(this.currentTaskId, task);
      if (task.status === 'processing') {
        this._showProgress();
        this._renderProgress(task.progress || 70, task.message || this.t('regenerating_translation'));
        this._startSSE(this.currentTaskId);
        return;
      }
      this._showResults(
        task.script,
        task.summary,
        task.video_title,
        task.translation,
        task.detected_language,
        task.summary_language,
        task.media_url,
        task.media_filename,
        task.timed_transcript,
        task.timed_translation,
      );
      this._renderDubProgress({ dub_status: 'completed', dub_progress: 100, dub_message: 'Translation regenerated' });
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
    } finally {
      this.regenerateTranslationBtn.disabled = false;
      this.regenerateTranslationBtn.innerHTML = originalHtml;
    }
  }

  async _regenerateTranscript() {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }
    if (!this.regenerateTranscriptBtn) return;

    const originalHtml = this.regenerateTranscriptBtn.innerHTML;
    this.regenerateTranscriptBtn.disabled = true;
    this.regenerateTranscriptBtn.innerHTML = `<span class="spinner"></span> <span>${this.t('regenerating_transcript')}</span>`;
    this._renderDubProgress({ dub_status: 'processing', dub_progress: 0, dub_message: this.t('regenerating_transcript') });
    this._startDubProgressPolling();

    try {
      const fd = new FormData();
      const sumLang = this.summaryLangSel?.value || 'vi';
      const apiKey = this.apiKeyInput?.value?.trim() || '';
      const baseUrl = this.modelBaseUrl?.value?.trim() || '';
      const modelId = this.modelSelect?.value || '';
      fd.append('summary_language', sumLang);
      if (apiKey) fd.append('api_key', apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id', modelId);

      const resp = await fetch(`${this.apiBase}/regenerate-transcript/${this.currentTaskId}`, { method: 'POST', body: fd });
      const task = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = typeof task.detail === 'string' ? task.detail : `HTTP ${resp.status}`;
        throw new Error(detail);
      }

      this._rememberRecentTask(this.currentTaskId, task);
      if (task.status === 'processing') {
        this._showProgress();
        this._renderProgress(task.progress || 55, task.message || this.t('regenerating_transcript'));
        this._startSSE(this.currentTaskId);
        return;
      }
      this._showResults(
        task.script,
        task.summary,
        task.video_title,
        task.translation,
        task.detected_language,
        task.summary_language,
        task.media_url,
        task.media_filename,
        task.timed_transcript,
        task.timed_translation,
      );
      this._renderDubProgress({ dub_status: 'completed', dub_progress: 100, dub_message: 'Transcript regenerated' });
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
    } finally {
      this.regenerateTranscriptBtn.disabled = false;
      this.regenerateTranscriptBtn.innerHTML = originalHtml;
    }
  }

  async _runHardSubtitleOcr() {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }
    if (!this.runHardSubtitleOcrBtn) return;

    const originalHtml = this.runHardSubtitleOcrBtn.innerHTML;
    this.runHardSubtitleOcrBtn.disabled = true;
    this.runHardSubtitleOcrBtn.innerHTML = `<span class="spinner"></span> <span>${this.t('running_hard_subtitle_ocr')}</span>`;
    this._hideError();
    this._showProgress();
    this._initSP();
    this._updateProgress(5, this.t('running_hard_subtitle_ocr'), true);

    try {
      const fd = new FormData();
      fd.append('summary_language', this.summaryLangSel?.value || 'vi');
      this._appendOcrSettings(fd);
      this._appendTaskSettings(fd);
      const apiKey = this.apiKeyInput?.value?.trim() || '';
      const baseUrl = this.modelBaseUrl?.value?.trim() || '';
      const modelId = this.modelSelect?.value || '';
      if (apiKey) fd.append('api_key', apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id', modelId);

      const resp = await fetch(`${this.apiBase}/hard-subtitle-ocr/${this.currentTaskId}`, { method: 'POST', body: fd });
      const task = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = typeof task.detail === 'string' ? task.detail : `HTTP ${resp.status}`;
        throw new Error(detail);
      }
      this._rememberRecentTask(this.currentTaskId, task);
      this._startSSE(this.currentTaskId);
      this._saveSettings();
      await this._persistActiveTaskSettings();
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
      this._hideProgress();
    } finally {
      this.runHardSubtitleOcrBtn.disabled = false;
      this.runHardSubtitleOcrBtn.innerHTML = originalHtml;
    }
  }

  async _resumeCurrentTask(triggerBtn = this.resumeTaskBtn) {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }
    if (!triggerBtn) return;

    const taskId = this.currentTaskId;
    const originalHtml = triggerBtn.innerHTML;
    triggerBtn.disabled = true;
    triggerBtn.innerHTML = `<span class="spinner"></span> <span>${this.t('resuming_task')}</span>`;
    this._hideError();
    this._showProgress();
    this._initSP();
    this._updateProgress(5, this.t('resuming_task'), true);

    try {
      const fd = new FormData();
      fd.append('summary_language', this.summaryLangSel?.value || 'vi');
      this._appendTaskSettings(fd);
      const apiKey = this.apiKeyInput?.value?.trim() || '';
      const baseUrl = this.modelBaseUrl?.value?.trim()?.replace(/\/$/, '') || '';
      const modelId = this.modelSelect?.value || '';
      const douyinCookie = this.douyinCookie?.value?.trim() || '';
      const bilibiliCookie = this.bilibiliCookie?.value?.trim() || '';
      if (apiKey) fd.append('api_key', apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id', modelId);
      if (douyinCookie) fd.append('douyin_cookie', douyinCookie);
      if (bilibiliCookie) fd.append('bilibili_cookie', bilibiliCookie);

      const resp = await fetch(`${this.apiBase}/resume-task/${encodeURIComponent(taskId)}`, { method: 'POST', body: fd });
      const task = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = typeof task.detail === 'string' ? task.detail : `HTTP ${resp.status}`;
        throw new Error(detail);
      }

      this._rememberRecentTask(taskId, task);
      this._startSSE(taskId);
    } catch (e) {
      this._showError(this.t('error_processing_failed') + e.message);
      this._setLoading(false);
    } finally {
      triggerBtn.disabled = false;
      triggerBtn.innerHTML = originalHtml;
    }
  }

  _cueRoleForExport(cue, index, speakerMode) {
    const midpoint = Number(cue.start || 0) + Math.max(0, Number(cue.end || 0) - Number(cue.start || 0)) / 2;
    for (const item of this._voiceOverridesForExport()) {
      if (midpoint >= Number(item.start || 0) && midpoint <= Number(item.end || 0)) return item.voice;
    }
    if (speakerMode === 'all_male') return 'male';
    if (speakerMode === 'all_female') return 'female';
    if (speakerMode === 'alternate') return index % 2 === 0 ? 'female' : 'male';
    return 'default';
  }

  async _exportDubbedVideo() {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }
    if (this.currentDubbedVideoUrl || this.currentDubbedVideoFilename) {
      this._downloadDubbedVideoFromTask();
      return;
    }

    const taskId = this.currentTaskId;
    const originalHtml = this.exportDubbedVideoBtn.innerHTML;
    this.currentDubbedVideoUrl = '';
    this.currentDubbedVideoFilename = '';
    this.exportDubbedVideoBtn.disabled = true;
    this.exportDubbedVideoBtn.innerHTML = `<span class="spinner"></span> <span>${this.t('exporting_dubbed_video')}</span>`;
    this._renderDubProgress({ dub_status: 'processing', dub_progress: 0, dub_message: this.t('dub_progress_prepare') });
    this._startDubProgressPolling(taskId);

    try {
      const fd = new FormData();
      const tts = this._getTtsSettings();
      const originalVolume = Math.max(0, Math.min(100, Number(tts.originalVolume || 25))) / 100;
      fd.append('mode', tts.audioMode === 'voiceover' ? 'voiceover' : 'replace');
      fd.append('original_volume', String(originalVolume));
      fd.append('include_subtitles', tts.includeSubtitles === 'on' ? 'true' : 'false');
      if (tts.includeSubtitles === 'on') {
        const subtitleSettings = this._getSubtitleSettings();
        fd.append('subtitle_mode', subtitleSettings.mode);
        fd.append('subtitle_source', subtitleSettings.source);
        fd.append('secondary_subtitle_source', subtitleSettings.secondarySource);
        fd.append('font_size', String(subtitleSettings.fontSize || 28));
        fd.append('text_color', subtitleSettings.textColor);
        fd.append('outline_color', subtitleSettings.outlineColor);
        fd.append('box_color', subtitleSettings.boxColor);
        fd.append('secondary_font_size', String(subtitleSettings.secondaryFontSize || 22));
        fd.append('secondary_text_color', subtitleSettings.secondaryTextColor);
        fd.append('secondary_outline_color', subtitleSettings.secondaryOutlineColor);
        fd.append('secondary_box_color', subtitleSettings.secondaryBoxColor);
        fd.append('position', subtitleSettings.position);
        fd.append('subtitle_x_offset', String(Number.isFinite(subtitleSettings.xOffset) ? subtitleSettings.xOffset : 0));
        fd.append('subtitle_y_offset', String(Number.isFinite(subtitleSettings.yOffset) ? subtitleSettings.yOffset : 0));
        fd.append('subtitle_merge_seconds', String(Number.isFinite(subtitleSettings.mergeSeconds) ? subtitleSettings.mergeSeconds : 0));
        fd.append('subtitle_time_offset', String(Number.isFinite(subtitleSettings.timeOffset) ? subtitleSettings.timeOffset : 0));
      }
      fd.append('speaker_mode', tts.speakerMode);
      fd.append('tts_provider', tts.provider);
      fd.append('tts_style', tts.style);
      fd.append('tts_merge_seconds', tts.mergeSeconds);
      fd.append('tts_max_fit_speed', tts.maxFitSpeed);
      fd.append('dub_timing_scale', tts.timingScale);
      fd.append('dub_audio_offset', tts.audioOffset);
      fd.append('default_voice', tts.defaultVoice);
      fd.append('male_voice', tts.maleVoice);
      fd.append('female_voice', tts.femaleVoice);
      fd.append('elevenlabs_api_key', tts.elevenLabsApiKey);
      fd.append('elevenlabs_model_id', tts.elevenLabsModelId);
      fd.append('elevenlabs_default_voice_id', tts.elevenLabsDefaultVoiceId);
      fd.append('elevenlabs_male_voice_id', tts.elevenLabsMaleVoiceId);
      fd.append('elevenlabs_female_voice_id', tts.elevenLabsFemaleVoiceId);
      fd.append('fpt_api_key', tts.fptApiKey);
      fd.append('fpt_speed', tts.fptSpeed);
      fd.append('fpt_default_voice', tts.fptDefaultVoice);
      fd.append('fpt_male_voice', tts.fptMaleVoice);
      fd.append('fpt_female_voice', tts.fptFemaleVoice);
      fd.append('voice_overrides', JSON.stringify(this._voiceOverridesForExport()));
      if (this.ttsDefaultClone?.files?.[0]) fd.append('default_clone', this.ttsDefaultClone.files[0]);
      if (this.ttsMaleClone?.files?.[0]) fd.append('male_clone', this.ttsMaleClone.files[0]);
      if (this.ttsFemaleClone?.files?.[0]) fd.append('female_clone', this.ttsFemaleClone.files[0]);
      const exportUrl = `${this.apiBase}/export-dubbed-video/${taskId}`;
      const resp = await fetch(exportUrl, { method: 'POST', body: fd });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`;
        throw new Error(detail);
      }

      const filename = data.filename || 'dubbed_video.mp4';
      const url = data.url || `${this.apiBase}/media/${encodeURIComponent(filename)}`;
      const completedTask = {
        task_id: taskId,
        dub_status: 'completed',
        dub_progress: 100,
        dub_message: 'Dubbed video is ready',
        dubbed_video_filename: filename,
        dubbed_video_url: url,
      };
      this._rememberRecentTask(taskId, completedTask);
      if (taskId === this.currentTaskId) {
        this.currentDubbedVideoFilename = filename;
        this.currentDubbedVideoUrl = url;
        this._renderDubProgress(completedTask);
        this._updateDubbedExportButton();
        this._downloadDubbedVideoFromTask();
      }
    } catch (e) {
      this._showError(this.t('error_dub_failed') + e.message);
      const errorTask = { task_id: taskId, dub_status: 'error', dub_progress: 0, dub_message: e.message };
      this._rememberRecentTask(taskId, errorTask);
      if (taskId === this.currentTaskId) this._renderDubProgress(errorTask);
    } finally {
      if (this.dubProgressTaskId === taskId) this._stopDubProgressPolling();
      await this._refreshDubProgress(taskId);
      this.exportDubbedVideoBtn.disabled = false;
      if (taskId === this.currentTaskId) {
        this._updateDubbedExportButton();
      } else {
        this.exportDubbedVideoBtn.innerHTML = originalHtml;
      }
    }
  }

  _renderDubProgress(task) {
    if (!this.dubProgressPanel) return;
    const status = task?.dub_status || 'processing';
    const progress = Math.max(0, Math.min(100, Number(task?.dub_progress || 0)));
    const message = task?.dub_message || this.t('dub_progress_prepare');

    this.dubProgressPanel.classList.add('show');
    this.dubProgressPanel.classList.toggle('error', status === 'error');
    this.dubProgressPanel.classList.toggle('done', status === 'completed');
    if (this.dubProgressFill) this.dubProgressFill.style.width = `${progress}%`;
    if (this.dubProgressStatus) this.dubProgressStatus.textContent = `${Math.round(progress)}%`;
    if (this.dubProgressMessage) this.dubProgressMessage.textContent = message;
  }

  _startDubProgressPolling(taskId = this.currentTaskId) {
    if (!taskId) return;
    this._stopDubProgressPolling();
    this.dubProgressTaskId = taskId;
    this.dubProgressTimer = setInterval(() => this._refreshDubProgress(taskId), 1000);
  }

  _stopDubProgressPolling() {
    if (this.dubProgressTimer) {
      clearInterval(this.dubProgressTimer);
      this.dubProgressTimer = null;
    }
    this.dubProgressTaskId = null;
  }

  async _refreshDubProgress(taskId = this.dubProgressTaskId || this.currentTaskId) {
    if (!taskId) return;
    try {
      const r = await fetch(`${this.apiBase}/task-status/${encodeURIComponent(taskId)}`);
      if (!r.ok) return;
      const task = await r.json();
      this._rememberRecentTask(taskId, task);
      if (!task.dub_status) return;
      if (task.dub_status === 'completed' || task.dub_status === 'error') {
        if (this.dubProgressTaskId === taskId) this._stopDubProgressPolling();
      }
      if (taskId !== this.currentTaskId) return;
      this.currentDubbedVideoUrl = task.dubbed_video_url || '';
      this.currentDubbedVideoFilename = task.dubbed_video_filename || '';
      this._renderDubProgress(task);
      this._updateDubbedExportButton();
    } catch (_) {}
  }

  /* ── UI helpers ───────────────────────────────────────── */
  _updateSubmitModeLabel() {
    if (!this.submitBtn || this.submitBtn.disabled) return;
    const downloadOnly = Boolean(this.downloadOnly?.checked);
    const hardSubtitleOcr = Boolean(this.hardSubtitleOcr?.checked);
    const icon = downloadOnly ? 'fas fa-download' : (hardSubtitleOcr ? 'fas fa-closed-captioning' : 'fas fa-search');
    const label = downloadOnly ? this.t('download_video') : (hardSubtitleOcr ? this.t('ocr_video') : this.t('start_transcription'));
    this.submitBtn.innerHTML = `<i class="${icon}"></i> <span>${label}</span>`;
  }

  _setLoading(on) {
    this.submitBtn.disabled = on;
    if (on) {
      this.submitBtn.innerHTML = `<span class="spinner"></span> ${this.t('processing')}`;
    } else {
      this._updateSubmitModeLabel();
    }
    if (this.uploadPickBtn) this.uploadPickBtn.disabled = on;
    if (this.uploadZone) {
      this.uploadZone.style.pointerEvents = on ? 'none' : '';
      this.uploadZone.style.opacity = on ? '0.65' : '';
      this.uploadZone.tabIndex = on ? -1 : 0;
    }
    if (this.fileInput) this.fileInput.disabled = on;
  }

  _showError(msg, options = {}) {
    this.errorMsg.textContent = msg;
    this.errorBanner.classList.add('show');
    const canResume = Boolean(options.canResume && this.currentTaskId);
    if (this.errorResumeTaskBtn) {
      this.errorResumeTaskBtn.style.display = canResume ? 'inline-flex' : 'none';
    }
    this.errorBanner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    if (!canResume) setTimeout(() => this._hideError(), 6000);
  }
  _hideError() {
    this.errorBanner.classList.remove('show');
    if (this.errorResumeTaskBtn) this.errorResumeTaskBtn.style.display = 'none';
  }

  _debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }
}

/* ── Boot ──────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  window.vt = new VideoTranscriber();
});

window.addEventListener('beforeunload', () => {
  if (window.vt) window.vt._stopSSE();
  if (window.vt?.dubProgressTimer) window.vt._stopDubProgressPolling();
});
