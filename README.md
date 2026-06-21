---
title: Aadhi Video Generator
emoji: 🎥
colorFrom: gray
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: AI Academic Video Generator
---

# 🎬 Aadhi: Cinematic AI Presentation Generator

A revolutionary, fully automated presentation and lecture generator powered by **Google Gemini 2.5 Pro** and the **Veo Video Model**. 

Aadhi dynamically converts boring PDF documents, text files, and textbook chapters into stunning, cinematic, interactive web presentations. It features perfectly paced AI voiceovers, live `Manim` mathematics simulations, hyper-realistic AI B-roll video, and a fully hands-free cinematic documentary viewing experience.

---

## ✨ Exhaustive Feature List

### 🧠 1. Intelligent Content Extraction (Gemini API)
*   **Deep Parsing:** Automatically ingests and parses large `.pdf`, `.docx`, and `.txt` files.
*   **Knowledge Structuring:** Uses Gemini to break down massive documents into hierarchical skill trees, concept maps, and logical pacing blocks.
*   **Dynamic Scriptwriting:** Generates a highly engaging, conversational script tailored for audio narration.

### 🎥 2. Cinematic Auto-Progression Engine
*   **Hands-Free Viewing:** Once you click "Play", the engine listens to the audio tracks and automatically transitions slides and animations in perfect sync. No clicking required.
*   **Ken Burns Effect:** A permanent, slow, infinite CSS `scale()` zoom applied to all background images and AI videos to keep the frame in constant motion.
*   **Post-Processing Overlays:** Features a digital 16:9 Letterbox, soft film grain, and a darkening vignette to simulate a movie theater experience.
*   **Global Progress Bar:** A sleek YouTube-style tracking bar runs across the bottom of the screen.

### 🎵 3. Advanced Audio & Subtitle Design
*   **Synchronized Voiceovers:** Integrated with `edge-tts` (and Gemini TTS) to provide buttery smooth narration.
*   **Kinetic Subtitles:** The script is chunked in real-time, displaying movie-style subtitles anchored to the bottom of the screen that fade in exactly as the words are spoken.
*   **Custom BGM:** A soft, looping background music track (`bgm.mp3`) rests beneath the voiceover at 4% volume to provide a professional, documentary-style ambiance.

### 🧮 4. Live Visual Engines
*   **Manim Simulations:** Automatically generates and executes raw Python code to mathematically animate formulas, geometry, and physics in real-time.
*   **Google Veo B-Roll:** Synthesizes stunning, highly detailed AI-generated cinematic B-roll footage mapped perfectly to the context of the slide using the Gemini API.
*   **Interactive Data Visualizations:** Dynamically spawns interactive `Chart.js` graphs, `JSXGraph` coordinate systems, hacker code terminals, and `Three.js` 3D models directly inside the presentation DOM.

---

## 🛠️ Tech Stack Architecture

### Frontend Layer
*   **Core:** Vanilla HTML5, CSS3, JavaScript (Zero heavy frameworks like React/Vue, ensuring lightning-fast load times).
*   **Styling:** Custom CSS Glassmorphism UI, CSS View Transitions API for seamless crossfades, and GSAP micro-animations.
*   **Libraries:** `Chart.js`, `JSXGraph`, `Three.js`, `Prism.js` (Syntax Highlighting), `MathJax` (LaTeX rendering).

### Backend & AI Layer
*   **Server Proxy:** Python `FastAPI` (Serves the UI and safely proxies API requests).
*   **AI Engine:** Google Gemini SDK (`gemini-2.5-pro` & `gemini-2.5-flash`).
*   **Video Render Engine:** `manim` (Community Edition).
*   **Audio Engine:** `edge-tts` (Microsoft Edge Voice API).

---

## 🚀 Local Installation & Setup

### 1. System Prerequisites
Ensure you have the following installed on your host machine:
*   **Python 3.10+**
*   **FFmpeg** (Crucial for Manim and Edge-TTS audio processing)
*   **MiKTeX / TeX Live** (Crucial for Manim LaTeX mathematical rendering)
*   **Git LFS** (Required to clone the large MP4/MP3 background assets)

### 2. Clone the Repository
Because this project contains large video and audio files, you **must** have Git LFS installed before cloning:
```bash
git lfs install
git clone https://github.com/SUBASH-R-007/Aadhi_prompt.git
cd Aadhi_prompt
```

### 3. Install Dependencies
Install the required Python backend libraries:
```bash
pip install -r requirements.txt
```

### 4. Environment Variables
Create a `.env` file in the root directory to store your secure credentials.
```bash
cp .env.example .env
```
Open `.env` and fill in your keys:
```env
# Gemini API Key for Veo Video Generation and Script Extraction
GEMINI_API_KEY=your_actual_api_key_here

# App Authentication (Optional for local, required for Hugging Face)
APP_USERNAME=kutty
APP_PASSWORD=koncepts
```

### 5. Start the Server
Boot up the FastAPI server, which will automatically host the frontend:
```bash
python server.py
```
Open your browser and navigate to: **http://127.0.0.1:8000**

---

## ☁️ Hugging Face Space Deployment

This repository is pre-configured with a `Dockerfile` and YAML frontmatter specifically designed to be deployed to **Hugging Face Spaces** effortlessly.

### Deployment Steps:
1. Create a new Hugging Face Space (Choose **Docker** as the SDK).
2. Connect this GitHub repository to the space, or push directly via Git CLI.
3. **CRITICAL:** Hugging Face restricts large files. Ensure all `.mp4`, `.mp3`, and `.png` files in the `video_template/` folder are tracked via `git lfs track`.
4. Go to the Space **Settings > Variables and secrets**.
5. Add the following **Secrets**:
    *   `GEMINI_API_KEY` (Your Google Gemini Key)
    *   `APP_USERNAME` (e.g., `admin`)
    *   `APP_PASSWORD` (e.g., `securepassword`)
6. The Dockerfile will automatically install the heavy OS dependencies (`ffmpeg`, `texlive-full`, `libcairo2-dev`), install the Python requirements, and expose the app on port `7860`.

---

## 💡 Usage Guide

1. **Upload Content:** Open the web interface and click the glowing upload zone to submit a PDF, DOCX, or TXT file.
2. **Configure Engine:** Use the right sidebar to select your preferred Gemini LLM model and `edge-tts` voice profile.
3. **Hardware Profile:** 
    *   *Option 3 (No AI Generation):* Ideal for fast, lightweight local testing without hitting the Gemini Veo quota.
    *   *Option 4 (Gemini API Video):* Securely pings the Gemini Veo model to stream beautiful cinematic videos using your `.env` key.
4. **Generate:** Click **Generate Presentation**. Sit back as the AI writes the script, renders the videos, and orchestrates the lecture.
5. **Watch Cinematic Mode:** Click the play button on the generated slide. Do not click next—the presentation will auto-progress perfectly in sync with the audio, subtitles, and background music.

---

## 🔐 Security Architecture

*   The `GEMINI_API_KEY` is securely loaded directly into the backend Python `FastAPI` process via the `.env` file.
*   Your API key is **never** exposed to the browser, the frontend payload, or the client-side JavaScript. 
*   All communications with Google's servers happen server-to-server.
*   The `/history` endpoint is secured behind Basic HTTP Authentication using `APP_USERNAME` and `APP_PASSWORD`.

---

## 📝 License

Distributed under the MIT License.
