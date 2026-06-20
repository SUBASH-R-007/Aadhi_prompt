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
# 🎬 Cinematic AI Presentation Generator

A revolutionary, fully automated presentation and lecture generator powered by Gemini 2.5 Pro and Google's Veo Video Model. This tool dynamically converts boring PDF documents and textbook chapters into stunning, cinematic, interactive web presentations with perfectly paced voiceovers, live Manim mathematics simulations, and hyper-realistic AI B-roll video....

## ✨ Core Features

*   **🧠 Intelligent Content Extraction:** Automatically parses large `.pdf`, `.docx`, and `.txt` files into hierarchical skill trees and concept maps using Gemini.
*   **🗣️ Human-like Voiceovers:** Integrated with `edge-tts` to provide buttery smooth, synchronized narration for every slide in the presentation.
*   **🧮 Live Manim Simulations:** Automatically generates raw Python code to mathematically animate formulas and geometry in real-time using Manim.
*   **🎥 Google Veo Video Generation:** Synthesizes stunning, highly detailed AI-generated cinematic B-roll footage mapped perfectly to the context of the slide using the Gemini API.
*   **📊 Interactive Data Visualizations:** Dynamically spawns interactive Chart.js graphs, JSXGraph coordinate systems, code hacker terminals, and 3D Three.js models on the fly.
*   **🎬 Cinematic UX:** Custom built in vanilla HTML/JS/CSS leveraging cutting-edge CSS View Transitions, `gsap` micro-animations, and kinetic typography for an unparalleled "movie-like" viewing experience.

## 🛠️ Tech Stack

*   **Frontend:** Vanilla HTML5, CSS3, JavaScript (No heavy frameworks!)
*   **Styling:** Custom CSS with Glassmorphism, CSS View Transitions API, and GSAP
*   **Backend Proxy:** Python `FastAPI` 
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
*   Google Gemini API Key

### 2. Installation

Clone the repository and install the backend dependencies:
```bash
git clone https://github.com/SUBASH-R-007/Aadhi_prompt.git
cd Aadhi_prompt
pip install -r requirements.txt
```

### 3. Environment Setup

Create a `.env` file in the root directory and add your Gemini API Key. You can use the provided template:
```bash
cp .env.example .env
```
Open `.env` and fill in your keys:
```env
# Gemini API Key for Veo Video Generation and Native Text-To-Speech
GEMINI_API_KEY=your_actual_api_key_here
```

### 4. Running the Application

Start the FastAPI backend server (which automatically serves the frontend interface):
```bash
python server.py
```
Open your browser and navigate to:
**http://127.0.0.1:8000**

## 💡 How to Use

1. **Upload Content:** Click the glowing upload zone and select a PDF or Document.
2. **Configure AI Engine:** Use the right sidebar to select your Gemini LLM model and edge-tts voice profile.
3. **Hardware Profile:** Select how you want AI videos to be generated:
    *   **Option 3 (No AI Generation):** Ideal for lightweight local testing.
    *   **Option 4 (Gemini API Video):** Will securely ping the Gemini Veo model to stream beautiful cinematic videos using your `.env` key.
4. **Generate:** Click "Start Server" to boot the backend, then hit **Generate Presentation**. Sit back as the AI writes the script, renders the videos, and orchestrates your cinematic lecture!

## 🔐 Security

*   The Gemini API Key is securely loaded from your local `.env` file directly into the backend Python process.
*   Your API key is **never** exposed to the browser or the frontend payload. 

## 📝 License

Distributed under the MIT License.
