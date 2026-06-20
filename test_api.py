import requests
import time

# URL of your new AI video generation endpoint
url = "http://127.0.0.1:8000/generate-ai-video"

# The prompt we want to test
payload = {
    "prompt": "A cinematic shot of a futuristic robot mascot explaining a complex topic, neon lights, 4k, masterpiece"
}

print("Sending request to generate AI video. This will take 3-6 minutes...")
start_time = time.time()

try:
    response = requests.post(url, json=payload, timeout=600)  # 10 minute timeout
    
    if response.status_code == 200:
        data = response.json()
        print(f"\nSuccess! Video generated in {round(time.time() - start_time, 1)} seconds.")
        print(f"Video URL: {data.get('video_url')}")
    else:
        print(f"\nFailed with status code: {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"\nError connecting to server: {e}")
