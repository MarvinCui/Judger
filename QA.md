# Judger validation record

Validated on October 24, 2026 (America/Los_Angeles).

## English keyboard demo

- 77 unit checks pass, covering question quality, seven-level caches, ordered feedback, automatic submission, completion guards, arbitrary historical correction, project isolation, file tools, Codex lifecycle, and live style restoration.
- Shipped Python, JavaScript, prompt files, and demo HTML contain no Chinese text. A regression check enforces that boundary; the Chinese README is intentionally localized.
- English questions are capped at 72 characters and 12 words. Known abstract jargon is rejected without regard to case. Questions are never made readable by simply truncating them.
- Both real-terminal checks pass: main/side input, rephrasing, back navigation, historical edits, separate project folders, C mode, bracket category switching, live adjustments, undo, and normal exit.
- Real Chrome checks pass with the English demo: Terminal keypresses change computed saturation from 1 to 1.2; cards receive three different colors; boldness changes heading weight to 640; undo restores 600; changing an earlier answer restores saturation to 0.8. Editing the actual HTML reloads the page. Chrome contains no Judger software controls, and typing = in Chrome does not control Judger.
- The real DeepSeek history-correction check passes in English: the initial Yes is written to audience.txt, an earlier answer is changed to No, and the same file is actually rewritten before completion.
- The real English DeepSeek-to-Codex flow passes: Judger automatically delegates a brief, sends an update while Codex is working, checks the actual English webpage title, and finishes with no outstanding feedback. This run used seven DeepSeek requests. Codex CLI version tested: 0.155.1.
- The real English main planner returns all 127 nodes for seven levels in one validated response; every question passes the English question rules.
- English and Chinese README pages render with all images loaded and no body overflow at a narrow viewport. The SVG banner also has a PNG export.

## Scope of these checks

The demo is functional software, not a validated human-computer-interaction result. These checks do not establish a productivity advantage, an optimal number of triggers, or optimal tree depth.

A complete seven-level binary question tree contains 127 nodes and 128 full paths. Planning and cache refills consume model usage and can introduce waiting. Only actual user answers become requirements.

All Judger interaction stays in Terminal. Browser preview currently supports static HTML and relative resources. Style changes can appear immediately; content rewrites and structural work wait for actual execution. React/Vite preview integration is not implemented.

Historical changes correct the executor's context and live styles. They do not automatically undo already executed commands or external actions.

EEG acquisition, decoding, classifier training, calibration, and hardware are future research. No EEG performance or hardware-cost claim has been validated here.

## Publishing boundary

Only source, tests, demo fixtures, documentation, brand assets, and the MIT license are intended for version control. Private local keys, runtime configuration, session records, QA screenshots, generated projects, and backups are excluded by .gitignore. The index audit passed for the exact 47 files prepared for the first push: the configured API key is absent, private runtime paths are excluded, and credential paths are ignored.
