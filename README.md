# 🎬 Aadhi Video Generator

A revolutionary, fully automated presentation and lecture generator powered by Gemini 2.5 Pro and Google's Veo Video Model. This tool dynamically converts boring PDF documents and textbook chapters into stunning, cinematic, interactive web documentaries with perfectly paced voiceovers, live Manim mathematics simulations, custom background music, and hyper-realistic AI B-roll video.

## ✨ Core Features

*   **🧠 Intelligent Content Extraction:** Automatically parses large `.pdf`, `.docx`, and `.txt` files into hierarchical skill trees and concept maps using Gemini.
*   **🗣️ Human-like Voiceovers:** Integrated with `edge-tts` and Gemini to provide buttery smooth, synchronized narration for every slide.
*   **🎥 The Cinematic UX Overhaul:**
    *   **Auto-Progression:** The presentation automatically flows from scene to scene, acting as a continuous, hands-free documentary video.
    *   **Custom Ambient Audio:** Seamlessly loops a copyright-free background music track (`bgm.mp3`) underneath the narration for a professional documentary atmosphere.
    *   **Kinetic Subtitles:** Movie-style subtitles at the bottom of the screen sync perfectly with the spoken voiceover in real-time.
    *   **Post-Processing Overlays:** Forces a 16:9 cinematic letterbox, deep vignettes, and realistic film-grain.
    *   **The Ken Burns Effect:** All static images and background media slowly scale and zoom to ensure the frame remains in constant, dynamic motion.
*   **🧮 Live Manim Simulations:** Automatically generates raw Python code to mathematically animate formulas and geometry in real-time using Manim.
*   **🎥 Google Veo Video Generation:** Synthesizes stunning, highly detailed AI-generated cinematic B-roll footage mapped perfectly to the context of the slide using the Gemini API.
*   **📊 Interactive Data Visualizations:** Dynamically spawns interactive Chart.js graphs, JSXGraph coordinate systems, code hacker terminals, and 3D Three.js models on the fly.
*   **📚 Persistent History Tracking:** Saves your previously generated projects and dynamically loads them on-demand via a custom Fast API backend.

## 🛠️ Tech Stack

*   **Frontend:** Vanilla HTML5, CSS3, JavaScript (No heavy frameworks!)
*   **Styling:** Custom CSS with Glassmorphism, CSS View Transitions API, and GSAP
*   **Backend Proxy:** Python `FastAPI`
*   **Database:** SQLite (`projects.db`)
*   **Video Engine:** `manim`
*   **AI Engine:** Google Gemini SDK (`gemini-2.5-pro` & `gemini-2.5-flash`)
*   **Audio Engine:** `edge-tts`
*   **Visual Libraries:** `Chart.js`, `JSXGraph`, `Three.js`, `Prism.js`, `MathJax`

## 🚀 Getting Started

### 1. Prerequisites
Ensure you have the following installed on your machine:
*   Python 3.10 or higher
*   [FFmpeg](https://ffmpeg.org/download.html) (Required for Manim and Edge-TTS audio processing)
*   [MiKTeX](https://miktex.org/) (Required for Manim LaTeX rendering)
*   [Git LFS](https://git-lfs.com/) (Required for handling large media files natively in the repository)
*   Google Gemini API Key

### 2. Installation

Clone the repository and install the backend dependencies:
```bash
git clone https://github.com/SUBASH-R-007/Aadhi_prompt.git
cd Aadhi_prompt

# Pull the large media assets (videos, MP3s, databases) using Git LFS
git lfs pull

pip install -r requirements.txt
```

### 3. Environment Setup

Create a `.env` file in the root directory and add your credentials. You can use the provided template:
```bash
cp .env.example .env
```
Open `.env` and fill in your keys:
```env
# Gemini API Key for Veo Video Generation, Text Extraction, and Native Text-To-Speech
GEMINI_API_KEY=your_actual_api_key_here

# App Authentication
APP_USERNAME=your_username
APP_PASSWORD=your_password
```

### 4. Running the Application

Start the FastAPI backend server (which automatically serves the frontend interface and acts as a media proxy):
```bash
python server.py
```
Open your browser and navigate to:
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
