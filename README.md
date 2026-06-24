# 🎬 Aadhi Video Generator

A revolutionary, fully automated presentation and lecture generator powered by Gemini 2.5 Pro and Google's Veo Video Model. This tool dynamically converts boring PDF documents and textbook chapters into stunning, cinematic, interactive web documentaries with perfectly paced voiceovers, live Manim mathematics simulations, custom background music, and hyper-realistic AI B-roll video.

## ✨ Core Features & Technical Breakthroughs

### 🧠 Intelligent Content & State Machine Extraction
* **Gemini Parsing Engine:** Automatically parses large `.pdf`, `.docx`, and `.txt` files into hierarchical skill trees, interactive concept maps, and strict JSON slide schemas.
* **Mascot State Machine:** Seamless crossfade video architecture for our mascot "Aadhi". The engine intelligently swaps between `idle`, `talking`, and `explaining_math` video layers based on the current slide's context, without breaking the visual flow.

### 🗣️ Audio & Subtitle Sync Engine
* **Sentence-by-Sentence Sync:** A bespoke mathematical character-length ratio algorithm parses the generated narration and syncs subtitles precisely sentence-by-sentence to the `timeupdate` of the audio stream.
* **Human-like Voiceovers:** Integrated with `edge-tts` to provide buttery smooth narration for every slide.
* **Ambient Ducking:** Seamlessly loops a copyright-free background music track (`bgm.mp3`), strictly leveled at 1% volume to provide professional documentary atmosphere without overpowering the narrator.

### 🎥 The Cinematic UX Overhaul
* **Auto-Progression:** The presentation automatically flows from scene to scene, acting as a continuous, hands-free documentary video.
* **Dynamic 3D Layout Engine:** The presentation board uses CSS 3D transforms (`rotateY`) and GSAP to simulate camera depth. 
* **Aspect-Ratio Aware Side Panels:** Side panels (`.dynamic-side-zone`) use native `fit-content` logic bounded by `25vw - 40vw` limits. Whether displaying a vertical image or an ultrawide video, the UI dynamically resizes to fit without ugly cropping.
* **TV-Broadcast Headers:** Modern, aesthetic slide headers and side-panel integration mimicking a high-end educational broadcast, fully replacing intrusive lower-thirds.
* **Integrated Control Bar:** A custom glassmorphic bottom bar houses playback controls, TTS speed sliders, and a fully integrated slide scrubber, hiding completely when not in use.

### 🧮 Live Manim & Mathematics Simulations
* **Automated Python Generation:** The LLM is strictly prompted to generate highly complex `manim_code` for geometric and algebraic proofs.
* **FastAPI Compilation:** The backend dynamically writes, executes, and compiles Manim Python scripts via local shell processes, serving the resulting `.mp4` directly back to the presentation board.

### 🎬 Media & Interactive Visualizations
* **Google Veo Video Generation:** Synthesizes stunning, highly detailed AI-generated cinematic B-roll footage mapped perfectly to the context of the slide using the Gemini API. Videos are served with `object-fit: contain` to prevent cinematic cropping.
* **Interactive Spawns:** Dynamically spawns interactive Chart.js graphs, JSXGraph coordinate systems, code hacker terminals, and 3D Three.js models on the fly.
* **Knowledge Check Quizzes:** Interactive multiple-choice quizzes are injected into the presentation flow, featuring an extended 10-second auto-reveal timer for seamless video recording workflows.

### 📚 Persistent History Tracking
* **SQLite Backend (`projects.db`):** Saves your previously generated projects, storing the full JSON schema payload.
* **Instant Replay:** A glassmorphic "History" modal allows you to dynamically load and reconstruct any previous presentation instantly via the Fast API backend.

## 🛠️ Tech Stack

*   **Frontend:** Vanilla HTML5, CSS3, JavaScript (No heavy frameworks!)
*   **Styling:** Custom CSS with Glassmorphism, CSS View Transitions API, and GSAP
*   **Backend Proxy:** Python `FastAPI`
*   **Database:** SQLite (`projects.db`)
*   **Video Engine:** `manim`
*   **AI Engine:** Google Gemini SDK (`gemini-2.5-pro` & `gemini-2.5-flash`)
*   **Audio Engine:** `edge-tts`
*   **Visual Libraries:** `Chart.js`, `JSXGraph`, `Three.js`, `Prism.js`, `MathJax`

## 🚀 Complete Setup Guide

### 1. System Prerequisites

Before running the application, you must install the following core dependencies. This project relies heavily on Manim and FFmpeg, which require system-level installations.

#### A. Python
* Download and install **Python 3.10+** from [python.org](https://www.python.org/downloads/).
* **Crucial:** During installation on Windows, ensure you check the box that says **"Add Python to PATH"**.

#### B. FFmpeg (Audio & Video Processing)
Required by Manim and `edge-tts` to process media.
* **Windows:**
  1. Download the latest `ffmpeg-release-essentials.zip` from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/).
  2. Extract the folder to your C: drive (e.g., `C:\ffmpeg`).
  3. Add `C:\ffmpeg\bin` to your system's **Environment Variables** (`PATH`).
* **Mac:** Run `brew install ffmpeg`
* **Linux:** Run `sudo apt install ffmpeg`

#### C. MiKTeX (For Manim LaTeX Rendering)
Required to dynamically generate and render mathematical equations and symbols.
* Download and install **[MiKTeX](https://miktex.org/download)**.
* **Crucial Configuration:** After installing, open the **MiKTeX Console**. Go to Settings, and ensure that **"Install missing packages on-the-fly"** is set to **Always** (or "Yes"). This prevents the server from crashing when Manim requests a specific LaTeX package it doesn't have yet.
* Ensure the MiKTeX `\bin` directory is added to your system `PATH`.

#### D. Git LFS (Large File Storage)
Required because this repository contains large `.mp4` background videos (Aadhi Mascot videos) and `.mp3` music files.
* Download and install from [git-lfs.com](https://git-lfs.com/).
* Open your terminal and run `git lfs install` to initialize it.

### 2. Installation

Once your system prerequisites are configured, clone the repository and install the Python backend dependencies:

```bash
# Clone the repository
git clone https://github.com/SUBASH-R-007/Aadhi_prompt.git
cd Aadhi_prompt

# Pull the large media assets (Mascot videos, MP3s) using Git LFS
git lfs pull

# Install all required Python packages (FastAPI, Manim, Edge-TTS, etc.)
pip install -r requirements.txt
```

### 3. Environment Setup

The backend relies on the Google Gemini API to orchestrate content extraction, voiceover scripts, and B-Roll video synthesis.

1. Create a `.env` file in the root directory by copying the template:
```bash
cp .env.example .env
```
2. Open `.env` and fill in your keys:
```env
# Gemini API Key for Veo Video Generation, Text Extraction, and TTS Scripting
GEMINI_API_KEY=your_actual_api_key_here

# OpenAI API Key (Optional, for GPT-4o Scripting and Voiceovers)
OPENAI_API_KEY=your_openai_api_key_here

# ElevenLabs API Key (Optional, for ultra-realistic Voiceovers)
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here

# App Authentication (Optional, used for locking down the web interface)
APP_USERNAME=your_username
APP_PASSWORD=your_password
```

### 4. Running the Application

Start the FastAPI backend server (which automatically serves the frontend interface, acts as a media proxy, and handles Manim compilation):

```bash
python server.py
```
*Wait until the terminal says `Uvicorn running on http://0.0.0.0:8000`.*

Finally, open your browser and navigate to:
**http://127.0.0.1:8000**

## 💡 How to Use

1. **Upload Content:** Click the glowing upload zone and select a PDF or Document.
2. **Configure AI Engine:** Use the right sidebar to select your Gemini LLM model and edge-tts voice profile.
3. **Hardware Profile:** Select how you want AI videos to be generated:
    *   **Option 3 (No AI Generation):** Ideal for lightweight local testing and fast generation.
    *   **Option 4 (Gemini API Video):** Will securely ping the Gemini Veo model to stream beautiful cinematic videos using your `.env` key.
4. **Generate:** Click "Start Server" to boot the backend, then hit **Generate Presentation**. 
5. **Sit Back & Watch:** Click play on the first slide and enjoy the fully automated, hands-free cinematic documentary!

## 🔐 Security & Data Management

*   **API Security:** The Gemini API Key is securely loaded from your local `.env` file directly into the backend Python process. Your API key is **never** exposed to the browser or the frontend payload. 
*   **Media Tracking:** Because this project generates large `.mp4` and `.mp3` files, it uses **Git Large File Storage (LFS)**. Standard `git commit` commands automatically package these large binaries correctly.

## 📝 License

Distributed under the MIT License.
