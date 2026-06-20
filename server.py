import os
import re
import subprocess
import uuid
import shutil
import hashlib
import traceback
import time
import edge_tts
from dotenv import load_dotenv

load_dotenv()

# --- Auto-Inject MiKTeX into PATH for Manim (Windows Fallback) ---
import shutil
if not shutil.which("latex"):
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", "")
    
    potential_paths = []
    if local_app_data:
        potential_paths.extend([
            os.path.join(local_app_data, r"Programs\MiKTeX\miktex\bin\x64"),
            os.path.join(local_app_data, r"Programs\MiKTeX 2.9\miktex\bin\x64"),
        ])
    if program_files:
        potential_paths.extend([
            os.path.join(program_files, r"MiKTeX\miktex\bin\x64"),
            os.path.join(program_files, r"MiKTeX 2.9\miktex\bin\x64"),
        ])
    
    for p in potential_paths:
        if os.path.exists(p):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + p
            break
# ----------------------------------------------

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, status, UploadFile, File
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncio
import secrets

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, os.getenv("APP_USERNAME", "admin"))
    correct_password = secrets.compare_digest(credentials.password, os.getenv("APP_PASSWORD", "secret"))
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

app = FastAPI()

# --- WebSocket Progress Tracking ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

ws_manager = ConnectionManager()

@app.websocket("/ws/progress")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
# -----------------------------------

# Enable CORS so the browser can make requests to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure static directory exists to serve videos
STATIC_DIR = "static_videos"
os.makedirs(STATIC_DIR, exist_ok=True)
FINAL_VIDEOS_DIR = "final_videos"
os.makedirs(FINAL_VIDEOS_DIR, exist_ok=True)
IMAGES_DIR = "images"
os.makedirs(IMAGES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/final_videos", StaticFiles(directory=FINAL_VIDEOS_DIR), name="final_videos")
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
app.mount("/video_template", StaticFiles(directory="video_template"), name="video_template")

@app.get("/", response_class=HTMLResponse)
async def get_index(username: str = Depends(verify_credentials)):
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/app.js", response_class=FileResponse)
async def get_app_js():
    if os.path.exists("app.js"):
        return FileResponse("app.js", media_type="application/javascript")
    raise HTTPException(status_code=404, detail="app.js not found")

import urllib.request
import urllib.parse

@app.get("/get-image")
async def get_image(prompt: str, subjectName: str = ""):
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
        
    # Create a clean cache key
    cache_key = hashlib.md5(prompt.encode('utf-8')).hexdigest()
    image_path = os.path.join(IMAGES_DIR, f"{cache_key}.jpg")
    
    if os.path.exists(image_path):
        return FileResponse(image_path, media_type="image/jpeg")
        
    # Doesn't exist, download it
    encoded_prompt = urllib.parse.quote(prompt)
    seed = int(time.time() * 1000) % 100000
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=1200&nologo=true&seed={seed}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response, open(image_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        return FileResponse(image_path, media_type="image/jpeg")
    except Exception as e:
        print(f"Failed to fetch image: {e}")
        # Try fallback
        try:
            fallback = urllib.parse.quote(f"{subjectName} abstract aesthetic background")
            fallback_url = f"https://image.pollinations.ai/prompt/{fallback}?width=800&height=1200&seed={seed}"
            req_fb = urllib.request.Request(fallback_url, headers=headers)
            with urllib.request.urlopen(req_fb) as response, open(image_path, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
            return FileResponse(image_path, media_type="image/jpeg")
        except:
            raise HTTPException(status_code=500, detail="Failed to generate image")

class RegenerateManimRequest(BaseModel):
    slide_title: str
    narration: str
    existing_code: str
    user_feedback: str | None = None

@app.post("/regenerate-manim")
async def regenerate_manim(request: RegenerateManimRequest, username: str = Depends(verify_credentials)):
    from google import genai
    from google.genai import types
    
    feedback_section = f"\n\nCRITICAL USER FEEDBACK - YOU MUST FOLLOW THIS:\n{request.user_feedback}\n" if request.user_feedback else ""
    
    prompt = f"""You are an expert Python Manim animator.
The user wants to REGENERATE the Manim code for the following presentation slide because the previous animation was inaccurate or had layout issues.
{feedback_section}

Slide Title: {request.slide_title}
Narration (Context): {request.narration}

Here is the OLD code that was generated:
```python
{request.existing_code}
```

Please generate a BRAND NEW, completely rewritten Manim script for this scene.
RULES:
1. ONLY output raw Python code. Do not wrap in markdown or backticks.
2. OVERLAP PREVENTION: You MUST use `VGroup(*elements).arrange(DOWN, aligned_edge=LEFT)` or explicitly set coordinates to completely prevent overlaps.
3. PREFER `MovingCameraScene`: Instead of a static `Scene`, use `MovingCameraScene` and animate `self.camera.frame.animate.move_to(obj)` to dynamically pan and zoom to important elements.
4. USE MORPHING: For mathematical formulas, strictly use `TransformMatchingTex` instead of `Transform` or `Write` to visually morph equations seamlessly.
5. HIGHLIGHTING: To emphasize concepts during the narration, use `SurroundingRectangle(element, color=YELLOW, buff=0.1)` and animate its creation.
6. Keep it highly cinematic and directly synchronized to the Narration context.
"""
    try:
        client = genai.Client()
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.4)
        )
        
        new_code = response.text.strip()
        if new_code.startswith("```python"):
            new_code = new_code[9:]
        if new_code.endswith("```"):
            new_code = new_code[:-3]
            
        return {"status": "success", "manim_code": new_code.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class RenderRequest(BaseModel):
    code: str

@app.post("/render")
async def render_manim(request: RenderRequest, username: str = Depends(verify_credentials)):
    original_code = request.code
    code = original_code
    
    max_retries = 2
    for attempt in range(max_retries + 1):
        # Auto-correct common LLM Manim positioning hallucinations
        replacements = {
            r'\.get_top_left\(\)': '.get_corner(UL)',
            r'\.get_top_right\(\)': '.get_corner(UR)',
            r'\.get_bottom_left\(\)': '.get_corner(DL)',
            r'\.get_bottom_right\(\)': '.get_corner(DR)',
            r'\.get_top_left\b': '.get_corner(UL)',
            r'\.get_top_right\b': '.get_corner(UR)',
            r'\.get_bottom_left\b': '.get_corner(DL)',
            r'\.get_bottom_right\b': '.get_corner(DR)',
            r'\.reverse_path\(\)': '.reverse_direction()',
            r'SVGMobject\([^)]+\)': 'Square(side_length=1).set_fill(WHITE, opacity=0.2)',
            r'ImageMobject\([^)]+\)': 'Rectangle(width=1.5, height=1).set_fill(WHITE, opacity=0.2)',
            r'GrowArrow\(': 'Create(',
            r',\s*scale_tips=True': '',
            r',\s*stroke_dash_2darray=\[[^\]]*\]': '',
        }
        for pattern, repl in replacements.items():
            code = re.sub(pattern, repl, code)
        
        # Auto-correct Rectangle(..., corner_radius=...) to RoundedRectangle(..., corner_radius=...)
        idx = 0
        while True:
            idx = code.find("Rectangle", idx)
            if idx == -1:
                break
            if idx > 0 and (code[idx-1].isalnum() or code[idx-1] == '_'):
                idx += 9
                continue
            start_paren = code.find("(", idx + 9)
            if start_paren == -1:
                idx += 9
                continue
            if code[idx+9:start_paren].strip() != "":
                idx += 9
                continue
            paren_count = 1
            scan_idx = start_paren + 1
            while scan_idx < len(code) and paren_count > 0:
                char = code[scan_idx]
                if char == '(':
                    paren_count += 1
                elif char == ')':
                    paren_count -= 1
                scan_idx += 1
            content = code[start_paren+1 : scan_idx-1]
            if "corner_radius" in content:
                code = code[:idx] + "RoundedRectangle" + code[idx+9:]
                idx += len("RoundedRectangle")
            else:
                idx += 9
        
        # Extract the Scene class name
        match = re.search(r'class\s+([A-Za-z0-9_]+)\(', code)
        if not match:
            raise HTTPException(status_code=400, detail="Could not find a valid Manim Scene class in the provided code.")
        
        scene_name = match.group(1)
        
        # Check Cache
        code_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()[:16]
        served_video_filename = f"{scene_name}_{code_hash}.mp4"
        served_video_path = os.path.join(STATIC_DIR, served_video_filename)
        
        if os.path.exists(served_video_path):
            print(f"\n[CACHE HIT] Instantly returning cached Manim video for scene: {scene_name}")
            return {
                "status": "success",
                "video_url": f"http://127.0.0.1:8000/static/{served_video_filename}",
                "cached": True
            }
        
        # Create py script
        py_filename = f"temp_{code_hash}.py"
        with open(py_filename, "w", encoding="utf-8") as f:
            if "from manim import" not in code:
                f.write("from manim import *\n")
                f.write("config.background_color = '#1A0B2E'\n")
                f.write(code)
            else:
                code = re.sub(r'(from manim import .*)', r'\1\nconfig.background_color = "#1A0B2E"\n', code, count=1)
                f.write(code)
        
        command = ["manim", "-qm", py_filename, scene_name]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            
            error_log = ""
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line_str = line.decode('utf-8', errors='replace').strip()
                if line_str:
                    error_log += line_str + "\n"
                    await ws_manager.broadcast(f"MANIM_LOG:{line_str}")
                    print(line_str)
                    
            await asyncio.wait_for(process.wait(), timeout=60)
            
            if process.returncode == 0:
                file_base = py_filename.replace(".py", "")
                generated_video_path = os.path.join("media", "videos", file_base, "720p30", f"{scene_name}.mp4")
                
                if not os.path.exists(generated_video_path):
                    raise HTTPException(status_code=500, detail="Manim completed, but video file was not found.")
                    
                shutil.copy(generated_video_path, served_video_path)
                return {
                    "status": "success",
                    "video_url": f"http://127.0.0.1:8000/static/{served_video_filename}",
                    "cached": False
                }
            else:
                # If it failed, attempt self-healing
                if attempt < max_retries:
                    msg = f"\n[AUTO-HEAL] Manim crashed! Asking Gemini to fix the code (Attempt {attempt+1}/{max_retries})..."
                    print(msg)
                    await ws_manager.broadcast(f"MANIM_LOG:{msg}")
                    
                    from google import genai
                    from google.genai import types
                    
                    fix_prompt = f"""You are a Python Manim expert.
The following Manim code failed to compile.
Here is the error log:
{error_log}

Here is the original code:
```python
{code}
```
Please fix the code. Ensure there are no overlapping elements, use .arrange(DOWN, aligned_edge=LEFT), and fix the exact error mentioned in the log.
Output ONLY the raw valid Python code, properly escaped, with NO markdown formatting, NO backticks, and NO explanations. Just the raw code.
"""
                    client = genai.Client()
                    # We assume gemini-2.5-flash is fast and cheap for quick healing
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=fix_prompt,
                        config=types.GenerateContentConfig(temperature=0.1)
                    )
                    
                    new_code = response.text.strip()
                    if new_code.startswith("```python"):
                        new_code = new_code[9:]
                    if new_code.endswith("```"):
                        new_code = new_code[:-3]
                        
                    code = new_code.strip()
                    continue # Try compiling again
                else:
                    raise HTTPException(status_code=500, detail=f"Manim Error after {max_retries} auto-heal attempts:\n{error_log}")
            
        except subprocess.TimeoutExpired as e:
            raise HTTPException(status_code=500, detail="Manim compilation timed out after 60 seconds (Infinite Loop or Crash).")
            
        finally:
            if os.path.exists(py_filename):
                os.remove(py_filename)

# --- AI Video Generation Endpoint ---
ltx_pipeline = "MANUAL"

class HardwareRequest(BaseModel):
    profile: str

gemini_api_key = None

@app.post("/start-ai-server")
def start_ai_server(request: HardwareRequest, username: str = Depends(verify_credentials)):
    global ltx_pipeline
    profile = request.profile
    
    if ltx_pipeline is not None:
        return {"status": "success", "message": "AI Server is already running!"}
        
    print(f"\n[INIT] Starting AI Server with Hardware Profile: {profile}")
    try:
        if profile == "rtx_3090_2x":
            from diffusers import LTXPipeline
            import torch
            ltx_pipeline = LTXPipeline.from_pretrained(
                "Lightricks/LTX-Video",
                torch_dtype=torch.bfloat16,
                device_map="balanced"
            )
            try:
                ltx_pipeline.enable_vae_slicing()
            except: pass
            print("Model loaded with Multi-GPU distribution (device_map='balanced') for 2x RTX 3090!")
            
        elif profile == "rtx_4060_laptop":
            from diffusers import LTXPipeline
            import torch
            ltx_pipeline = LTXPipeline.from_pretrained(
                "Lightricks/LTX-Video",
                torch_dtype=torch.bfloat16
            )
            ltx_pipeline.to("cuda")
            try:
                # Critical optimizations for 8GB VRAM
                ltx_pipeline.enable_model_cpu_offload()
                ltx_pipeline.enable_vae_tiling()
                ltx_pipeline.enable_vae_slicing()
            except: pass
            print("Model loaded with CPU offloading and VAE tiling for 8GB VRAM RTX 4060 Laptop!")
            
        elif profile == "gemini_api_video":
            if not os.getenv("GEMINI_API_KEY"):
                raise Exception("GEMINI_API_KEY environment variable is not set. Please add it to your .env file.")
            ltx_pipeline = "GEMINI_API"
            print("Gemini API configured for Veo video generation!")
            
        elif profile == "no_ai_generation":
            ltx_pipeline = "MANUAL"
            print("Server started without AI video generation capabilities.")
            
        else:
            raise HTTPException(status_code=400, detail="Invalid hardware profile.")
            
        return {"status": "success", "message": "AI Server successfully initialized."}
    except Exception as e:
        traceback.print_exc()
        error_msg = str(e)
        if isinstance(e, ImportError):
            error_msg = "Missing required AI libraries (e.g. diffusers, torch). Please install them, or select 'Option 3: No AI Generation'."
        raise HTTPException(status_code=500, detail=error_msg)

class AIVideoRequest(BaseModel):
    prompt: str

@app.post("/generate-ai-video")
async def generate_ai_video(request: AIVideoRequest, username: str = Depends(verify_credentials)):
    global ltx_pipeline
    
    if ltx_pipeline is None:
        raise HTTPException(status_code=400, detail="AI Server is offline. Please start the AI Server from the webpage first.")
        
    prompt = request.prompt
    
    # 1. Check Cache
    # Create a unique, deterministic hash for this prompt
    prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16]
    filename = f"ai_video_{prompt_hash}.mp4"
    filepath = os.path.join(STATIC_DIR, filename)
    

        
    if os.path.exists(filepath):
        if os.path.getsize(filepath) > 0:
            print(f"\n[CACHE HIT] Instantly returning existing video for prompt: '{prompt[:30]}...'")
            return {
                "status": "success",
                "video_url": f"http://127.0.0.1:8000/static/{filename}",
                "cached": True
            }
        else:
            os.remove(filepath)
        
    if ltx_pipeline == "MANUAL":
        print(f"\n[MANUAL REQUIRED] User needs to manually generate {filename}")
        return {
            "status": "manual_required",
            "filename": filename,
            "prompt": prompt
        }
        
    if ltx_pipeline == "GEMINI_API":
        print(f"\n[GEMINI API] Calling Google Veo model for prompt: '{prompt[:50]}...'")
        import time
        from google import genai
        from google.genai import types
        
        try:
            api_key = os.getenv("GEMINI_API_KEY")
            client = genai.Client(api_key=api_key)
            operation = client.models.generate_videos(
                model="veo-2.0-generate-001",
                prompt=prompt,
                config=types.GenerateVideosConfig(
                    aspect_ratio="16:9",
                    person_generation="ALLOW_ADULT",
                )
            )
            print("Video generation operation started. Polling until complete... this may take 1-2 minutes.")
            
            start_time = time.time()
            timeout_seconds = 300 # 5 minutes maximum timeout
            
            while not operation.done:
                if time.time() - start_time > timeout_seconds:
                    raise HTTPException(status_code=504, detail="Veo API generation timed out after 5 minutes. Request aborted to prevent hanging.")
                time.sleep(10)
                operation = client.operations.get(operation=operation)
                print(".", end="", flush=True)
                
            print("\nOperation complete!")
            
            if operation.response and operation.response.generated_videos:
                generated_video = operation.response.generated_videos[0]
                # Download and save the video bytes
                video_bytes = client.files.get(name=generated_video.video.name).download()
                with open(filepath, "wb") as f:
                    f.write(video_bytes)
                print(f"Video saved to {filepath}")
                
                return {
                    "status": "success",
                    "video_url": f"http://127.0.0.1:8000/static/{filename}"
                }
            else:
                raise Exception("No video was returned from Veo API.")
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Gemini Veo API error: {str(e)}")


    negative_prompt = "worst quality, inconsistent, blurry, deformed, text, watermark, mutated, extra limbs, bad anatomy, low resolution, artifacts"

    print(f"\n[GENERATING] Creating AI video for prompt: '{prompt}'")
    
    try:
        # Generate the ultimate continuous high-quality chunk
        # Using Native 704p (1280x704) for maximum physical realism (must be divisible by 32).
        # Maxed out 257 frames (~10.2 seconds at 25fps)
        import torch
        generator = torch.Generator().manual_seed(42)
        
        video_frames = ltx_pipeline(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=1280,
            height=704,
            num_frames=257,
            num_inference_steps=60, # Extreme detail refinement
            guidance_scale=4.5, # Forces strict adherence to the cinematic prompt
            generator=generator
        ).frames[0]
        
        # Clean up VRAM just in case
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()
            
        from diffusers.utils import export_to_video
        export_to_video(video_frames, filepath, fps=25)
        print(f"\n[SUCCESS] Video stitched and saved to cache at {filepath}")
        
        return {
            "status": "success",
            "video_url": f"http://127.0.0.1:8000/static/{filename}",
            "cached": False
        }
    except Exception as e:
        traceback.print_exc()
        print(f"\n[ERROR] Failed generating AI video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class AudioRequest(BaseModel):
    text: str
    voice: str = "en-US-GuyNeural"  # Default fallback
    tts_engine: str = "default"

class ImageUploadRequest(BaseModel):
    base64_data: str

class HistoryRequest(BaseModel):
    subject_name: str
    unit_name: str | None = None
    session_number: str | None = None
    session_title: str | None = None
    concept_map: list | None = None
    scenes: list | None = None

@app.post("/generate-audio")
async def generate_audio(request: AudioRequest, username: str = Depends(verify_credentials)):
    text = request.text.strip()
    voice = request.voice
    tts_engine = request.tts_engine
    
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    
    # Cache based on text, voice, and engine hash
    hash_str = f"{text}_{voice}_{tts_engine}"
    text_hash = hashlib.sha256(hash_str.encode('utf-8')).hexdigest()[:16]
    
    # Gemini returns .wav implicitly, Edge-TTS returns .mp3
    file_ext = "wav" if tts_engine.startswith("gemini") else "mp3"
    filename = f"audio_{text_hash}.{file_ext}"
    filepath = os.path.join(STATIC_DIR, filename)
    
    if os.path.exists(filepath):
        if os.path.getsize(filepath) > 0:
            # Invalidate raw PCM files that lack the RIFF WAV header
            if filepath.endswith(".wav"):
                with open(filepath, "rb") as f:
                    header = f.read(4)
                if header != b'RIFF':
                    print(f"Invalidating raw PCM cache without WAV header: {filepath}")
                    os.remove(filepath)
            
            if os.path.exists(filepath):
                print(f"\n[CACHE HIT] Instantly returning cached audio for: '{text[:30]}...'")
                return {"status": "success", "audio_url": f"http://127.0.0.1:8000/static/{filename}?t={int(time.time())}"}
        else:
            os.remove(filepath)
            
    try:
        if tts_engine.startswith("gemini"):
            # Use the dedicated TTS preview model for audio generation
            actual_model = 'gemini-2.5-flash-preview-tts'
            print(f"\n[GEMINI TTS] Generating voice '{voice}' using model '{actual_model}'...")
            from google import genai
            from google.genai import types
            
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise Exception("GEMINI_API_KEY is not set in the .env file.")
                
            client = genai.Client(api_key=api_key)
            
            # The preview TTS model requires explicit instructions to read text
            prompt_text = f"Please read the following text aloud with high energy, a natural conversational tone, and a fluent Indian English accent:\n{text}"
            
            response = client.models.generate_content(
                model=actual_model,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice
                            )
                        )
                    )
                )
            )
            
            audio_data = None
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'inline_data') and part.inline_data and part.inline_data.data:
                            audio_data = part.inline_data.data
                            break
                if audio_data:
                    break
                    
            if not audio_data:
                raise Exception(f"No audio data returned. Response: {response}")
                
            if tts_engine.startswith("gemini") and not audio_data.startswith(b'RIFF'):
                import wave
                with wave.open(filepath, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2) # 16-bit
                    wav_file.setframerate(24000)
                    wav_file.writeframes(audio_data)
            else:
                with open(filepath, "wb") as f:
                    f.write(audio_data)
                
            return {"status": "success", "audio_url": f"http://127.0.0.1:8000/static/{filename}?t={int(time.time())}"}
            
        else:
            # Default Edge-TTS fallback
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(filepath)
            return {"status": "success", "audio_url": f"http://127.0.0.1:8000/static/{filename}?t={int(time.time())}"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n[WARNING] Gemini TTS failed with error: {e}. Falling back to default Edge-TTS...")
        try:
            communicate = edge_tts.Communicate(text, "en-US-GuyNeural")
            await communicate.save(filepath)
            return {"status": "success", "audio_url": f"http://127.0.0.1:8000/static/{filename}?t={int(time.time())}"}
        except Exception as fallback_err:
            raise HTTPException(status_code=500, detail=f"Gemini and fallback TTS both failed: {fallback_err}")

@app.post("/upload-image")
async def upload_image(request: ImageUploadRequest, username: str = Depends(verify_credentials)):
    import base64
    import uuid
    
    try:
        b64_string = request.base64_data
        if "," in b64_string:
            b64_string = b64_string.split(",", 1)[1]
            
        # Fix missing padding if any
        b64_string += "=" * ((4 - len(b64_string) % 4) % 4)
        
        image_data = base64.b64decode(b64_string)
        filename = f"img_{uuid.uuid4()}.png"
        filepath = os.path.join(STATIC_DIR, filename)
        
        with open(filepath, "wb") as f:
            f.write(image_data)
            
        print(f"\n[IMAGE EXTRACTED] Saved DOCX image to {filepath}")
        return {"status": "success", "url": f"/static/{filename}"}
        
    except Exception as e:
        print(f"[ERROR] Image upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

STATIC_VIDEOS_DIR = STATIC_DIR  # alias

@app.post("/upload-media")
async def upload_media(file: UploadFile = File(...), username: str = Depends(verify_credentials)):
    try:
        os.makedirs(STATIC_VIDEOS_DIR, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:8]}_{file.filename}"
        filepath = os.path.join(STATIC_VIDEOS_DIR, filename)
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        print(f"\n[MEDIA UPLOADED] Saved user media to {filepath}")
        return {"status": "success", "url": f"/static/{filename}"}
    except Exception as e:
        print(f"[ERROR] Media upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/save-history")
async def save_history(request: HistoryRequest, username: str = Depends(verify_credentials)):
    timestamp = int(time.time())
    safe_name = "".join([c if c.isalnum() else "_" for c in request.subject_name.strip()]).strip("_")
    if not safe_name:
        safe_name = "presentation"
    
    filename = f"{safe_name}_{timestamp}.html"
    filepath = os.path.join(FINAL_VIDEOS_DIR, filename)
    
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Could not read index.html template.")
        
    payload = {
        "subject_name": request.subject_name,
        "unit_name": request.unit_name,
        "session_number": request.session_number,
        "session_title": request.session_title,
        "concept_map": request.concept_map,
        "scenes": request.scenes
    }
    
    import json
    json_data = json.dumps(payload).replace("</script>", "<\\/script>")
    injected_script = f'<base href="/"><script id="injected-data">window.INJECTED_DATA = {json_data};</script>'
    
    if '<script id="injected-data">window.INJECTED_DATA = null;</script>' in html_content:
        html_content = html_content.replace('<script id="injected-data">window.INJECTED_DATA = null;</script>', injected_script)
    else:
        html_content = html_content.replace("</head>", f"{injected_script}\n</head>")
        
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"\n[HISTORY SAVED] Presentation exported to {filepath}")
    return {"status": "success", "url": f"/final_videos/{filename}"}

@app.get("/get-history")
async def get_history(username: str = Depends(verify_credentials)):
    import glob
    files = glob.glob(f"{FINAL_VIDEOS_DIR}/*.html")
    history = []
    for f in files:
        filename = os.path.basename(f)
        parts = filename.rsplit("_", 1)
        if len(parts) == 2 and parts[1].replace(".html", "").isdigit():
            name = parts[0].replace("_", " ")
            ts = int(parts[1].replace(".html", ""))
            history.append({"url": f"/final_videos/{filename}", "name": name, "timestamp": ts})
        else:
            history.append({"url": f"/final_videos/{filename}", "name": filename, "timestamp": 0})
            
    history.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"history": history}

class ScriptRequest(BaseModel):
    prompt_text: str
    model_name: str = "gemini-2.5-flash"

@app.post("/generate-script")
async def generate_script(request: ScriptRequest, username: str = Depends(verify_credentials)):
    from google import genai
    from google.genai import types
    import json
    import re
    from fastapi.responses import StreamingResponse
    import traceback
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not set in the .env file.")
        
    client = genai.Client(api_key=api_key)
    # Ensure both sync and async underlying httpx clients have a large timeout
    import httpx
    if hasattr(client._api_client, '_async_httpx_client') and client._api_client._async_httpx_client:
        client._api_client._async_httpx_client.timeout = httpx.Timeout(1200.0)
    if hasattr(client._api_client, '_httpx_client') and client._api_client._httpx_client:
        client._api_client._httpx_client.timeout = httpx.Timeout(1200.0)
    
    async def stream_generator():
        import queue
        import threading
        
        q = queue.Queue()
        
        def sync_worker():
            try:
                # Use standard synchronous streaming, avoiding httpx async max_line_length bug!
                response_stream = client.models.generate_content_stream(
                    model=request.model_name,
                    contents=request.prompt_text,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        max_output_tokens=65536,
                        temperature=0.2
                    )
                )
                for chunk in response_stream:
                    if chunk.text:
                        q.put(("chunk", chunk.text))
                q.put(("done", None))
            except Exception as e:
                q.put(("error", e))

        thread = threading.Thread(target=sync_worker)
        thread.start()
        
        try:
            # Yield headers immediately to prevent browser connection drops
            yield "STATUS:STARTED\n"
            
            raw_text = ""
            total_chars = 0
            chunk_count = 0
            
            while True:
                # Slight yield to event loop
                await asyncio.sleep(0.02)
                try:
                    msg_type, data = q.get_nowait()
                except queue.Empty:
                    continue
                    
                if msg_type == "chunk":
                    raw_text += data
                    total_chars += len(data)
                    chunk_count += 1
                    # Send a progress heartbeat via stream every chunk to keep connection alive
                    yield f"PROGRESS:{total_chars}\n"
                    # Also broadcast to websocket for external listeners
                    if chunk_count % 5 == 0:
                        await ws_manager.broadcast(f"GEMINI_PROGRESS:{total_chars}")
                elif msg_type == "done":
                    break
                elif msg_type == "error":
                    yield f"ERROR:{str(data)}\n"
                    return
            
            # Process the full JSON result
            print(f"\n[GEMINI RESPONSE] Received {total_chars} chars total")
            raw_text = raw_text.strip()
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text, re.DOTALL)
            if match:
                raw_text = match.group(1).strip()
            else:
                # If no backticks, just strip leading/trailing whitespace
                raw_text = raw_text.strip()
                
            try:
                # raw_decode parses the FIRST valid JSON object and ignores everything after it!
                # It is mathematically immune to "Extra data" errors.
                parsed, _ = json.JSONDecoder().raw_decode(raw_text.lstrip())
                final_json_string = json.dumps(parsed) # Ensure it's a single line
                yield f"FINAL_JSON:{final_json_string}\n"
            except Exception as parse_err:
                print(f"[PARSE ERROR] {parse_err}")
                try:
                    with open("debug_failed_json.txt", "w", encoding="utf-8") as f:
                        f.write(raw_text)
                    print("Dumped failed JSON to debug_failed_json.txt")
                except:
                    pass
                yield f"ERROR:Failed to parse JSON response: {str(parse_err)}\n"
        except Exception as e:
            traceback.print_exc()
            yield f"ERROR:{str(e)}\n"
            
    return StreamingResponse(stream_generator(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    print("Starting Manim Rendering Backend on http://127.0.0.1:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
