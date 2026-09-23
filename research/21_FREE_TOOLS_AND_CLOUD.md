# 21 — Free tools & cloud for students (checked 23 Sep 2026)

Idea: spend **money = ₹0**. Put each job on the free tool that's best at it, and save paid Claude credits for building the code.

## 1. Claim these first (15 minutes each, huge value)

| Offer | What you get | How | Why it matters for us |
|---|---|---|---|
| **GitHub Student Developer Pack** | **Free GitHub Pro while a student**, GitHub Copilot Student, Codespaces at Pro level, **$100 Azure credit** (18+), $100 DigitalOcean credit, JetBrains IDEs, free domains (.me via Namecheap) | education.github.com/pack → verify with BVRIT email / ID card | **GitHub Pro = protected branches on a PRIVATE repo** (required-review pull requests). Free accounts can't enforce that on private repos |
| **Google AI Plus — free 12 months for Indian students** (announced Aug 2026) | Gemini with higher limits, **Deep Research**, **NotebookLM** higher limits, 400 GB storage | Google One student offer page → verify via SheerID | Teammates' free research engine. Upload our `research/` folder into **NotebookLM** → ask questions / get an **audio overview** (listen to the research like a podcast instead of reading it) |
| **Qualcomm AI Hub** | "Currently completely free": compile, profile on real Qualcomm devices, on-device accuracy checks, download | workbench.aihub.qualcomm.com (Qualcomm ID) | Latency on QCS6490 for the PPT |
| **Edge Impulse Developer plan** (Qualcomm-owned) | Free: GPU training (60-min jobs), 3 private projects, 3 collaborators, FOMO / FOMO-AD / YOLO-Pro | edgeimpulse.com | tinyML on the MCU/UNO Q side, Qualcomm-aligned |

## 2. Free GPUs for training

| Platform | Free GPU | Notes |
|---|---|---|
| **Kaggle** | ~30 GPU hours/week (T4 ×2 or P100) — check your quota page, it varies | Our notebooks are already there; best for the SKU-110K shelf detector |
| **Google Colab (free)** | T4 when available, session limits vary | Good for quick experiments |
| **Lightning AI** | Some free monthly GPU credits (amount changes — third-party sites say ~22 GPU-hours/month; verify on sign-up) | Persistent studio, nicer than Colab |
| **Azure (Student Pack)** | $100 credit | Keep for a small cloud "HQ" server demo, not for GPUs |

## 3. Free tools by job

| Job | Tool | Free tier notes |
|---|---|---|
| Labelling images | **CVAT** (self-host or cvat.ai), **Label Studio** (open source) | Private, unlimited |
| Labelling + auto-label + dataset hosting | **Roboflow** Public plan | 15 credits/month, **data becomes public** on Universe; research credits via research.roboflow.com — don't upload anything with people's faces |
| Zones / lines / tracking code | **supervision** (Roboflow, MIT) | pip install |
| Simulate STM32 Blue Pill without hardware | **Wokwi** (browser; Blue Pill part supported) | Test UART protocol + logic before flashing |
| PCB design | **KiCad** | Free, open source |
| MQTT broker | **Mosquitto** | Free |
| Dashboards/graphs (optional) | **Grafana** OSS, **Node-RED** | Free |
| Experiment tracking | **Weights & Biases** (free personal/academic) | Log training runs |
| Demo hosting | **Hugging Face Spaces** (free CPU) | Host a replay demo of the dashboard for judges |
| AI coding | GitHub Copilot Student (via pack; note: reports say new Copilot Student sign-ups were limited from Apr 2026 — check), Claude Code (paid, you already use) | |
| Research | Claude (friend's account, see `FRIEND_RESEARCH_PROMPT.md`), Gemini Deep Research (Google AI Plus), NotebookLM (read our docs for you) | |
| Slides/design | Canva (education plan if your college has it), Figma education | |

## 4. How to split AI credits across the team

| Person | Tool | Job |
|---|---|---|
| You (Vinith) | Claude Code on laptop | **Implementation only** (code, tests, evaluation) |
| Friend 1 | Claude (their account) | Deep research from `FRIEND_RESEARCH_PROMPT.md` → report file → PR to GitHub |
| Friend 2 | Gemini Deep Research (free student plan) | Second opinion on the same research questions (cross-check facts) |
| Everyone | NotebookLM | Load `research/` + `WORK_LOG.md` + `HANDOFF_FOR_CLAUDE.md` → audio overview, Q&A, viva practice ("ask me judge questions") |
| Embedded person | Wokwi + Edge Impulse | Firmware simulation + tinyML |
| Data person | Kaggle + CVAT | Shelf dataset + training |

## 5. Using the cloud *without* breaking our "edge" story

- **The cloud never processes video.** It only gets hourly aggregates (optional HQ dashboard).
- Tiny demo HQ: one Azure (Student Pack) or free-tier VM running Mosquitto + a dashboard that receives aggregates from the Pi → shows "multi-store view" at the finale.
- Training happens in the cloud (Kaggle/Colab); **inference always on the edge**. Say exactly this in the viva.

## Sources
- GitHub Student Pack: https://education.github.com/pack · partners FAQ: https://github.com/github-education-resources/Student-Developer-Pack-Current-Partners-FAQ · protected branches plans: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- Google AI Plus for Indian students: https://www.business-standard.com/technology/tech-news/google-ai-plus-free-india-students-gemini-study-tools-126082000453_1.html
- Qualcomm AI Hub FAQ (free): https://workbench.aihub.qualcomm.com/docs/hub/faq.html
- Edge Impulse Developer plan: https://www.edgeimpulse.com/blog/introducing-the-developer-plan/
- Kaggle GPU quota (varies): https://www.kaggle.com/docs/efficient-gpu-usage · Lightning AI students: https://lightning.ai/docs/team-management/academia/students
- Roboflow pricing: https://roboflow.com/pricing · Wokwi Blue Pill: https://docs.wokwi.com/parts/board-stm32-bluepill
- Copilot Student sign-up note: https://github.com/orgs/community/discussions/158175
