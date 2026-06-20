import re

with open('index.html', 'r', encoding='utf-16') as f:
    content = f.read()

# Find the start
start_match = re.search(r'async function speakNarration\(text, syncElements = \[\]\) \{', content)
end_match = re.search(r'const conceptMapPanel = document\.getElementById\(\'concept-map-panel\'\);', content)

if not start_match or not end_match:
    print('Could not find start or end bounds.')
else:
    # Safely find the bounds
    start_idx = content.rfind('\n', 0, start_match.start()) + 1
    if start_idx == 0: start_idx = start_match.start()
    
    end_idx = content.rfind('\n', 0, end_match.start()) + 1
    if end_idx == 0: end_idx = end_match.start()
    
    # CRITICAL FIX: Ensure start_idx is actually before end_idx!
    # Because there are MULTIPLE concept-map-panels in the 480KB file!
    # Let's find the FIRST conceptMapPanel AFTER the start_idx!
    real_end_idx = content.find("const conceptMapPanel = document.getElementById('concept-map-panel');", start_idx)
    
    if real_end_idx == -1:
        print("Could not find end bounds after start.")
    else:
        end_idx = content.rfind('\n', start_idx, real_end_idx) + 1
        
        new_block = """        async function speakNarration(text, syncElements = []) {
            const thisNarrationId = ++window.currentNarrationId;

            const triggerFallbackReveal = () => {
                if (syncElements && syncElements.length > 0) {
                    syncElements.forEach((el, index) => {
                        setTimeout(() => {
                            el.classList.remove('cascade-hidden');
                            el.classList.add('cascade-visible');
                        }, index * 400 + 200);
                    });
                }
            };

            if (window.currentAudio) {
                window.currentAudio.pause();
                if (window.currentAudio.parentNode) {
                    window.currentAudio.parentNode.removeChild(window.currentAudio);
                }
                window.currentAudio = null;
            }
            if (window.speechSynthesis.speaking) {
                window.speechSynthesis.cancel();
            }

            if (ttsState.isMuted || !text) {
                triggerFallbackReveal();
                return;
            }

            const ttsEngine = document.getElementById('tts-engine-select') ? 
document.getElementById('tts-engine-select').value : 'default';
            const selectedVoice = ttsEngine === 'default' 
                                    ? document.getElementById('voice-select').value 
                                    : document.getElementById('gemini-voice-select').value;
            
            // -----------------------------------------------------
            // NEW REAL-TIME API CHUNKING ENGINE (100% PERFECT SYNC)
            // -----------------------------------------------------
            
            const chunks = [];
            let lastCutIndex = 0;
            const narrationLower = text.toLowerCase();
            
            if (syncElements && syncElements.length > 0) {
                syncElements.forEach((el, index) => {
                    const elText = el.textContent.trim().toLowerCase();
                    const cleanText = elText.replace(/[.,/#!$%^&*;:{}=\-_`~()]/g,"");
                    const words = cleanText.split(/\\s+/).filter(w => w.length > 3).slice(0, 3);
                    
                    let foundIndex = -1;
                    if (words.length > 0) {
                        foundIndex = narrationLower.indexOf(words.join(" "), lastCutIndex);
                        if (foundIndex === -1 && words.length >= 2) {
                            foundIndex = narrationLower.indexOf(words.slice(0, 2).join(" "), lastCutIndex);
                        }
                        if (foundIndex === -1) {
                            foundIndex = narrationLower.indexOf(words[0], lastCutIndex);
                        }
                    }
                    
                    if (foundIndex !== -1 && foundIndex > lastCutIndex) {
                        const chunkText = text.substring(lastCutIndex, foundIndex).trim();
                        if (chunkText) {
                            chunks.push({ text: chunkText, elementToReveal: null });
                        }
                        lastCutIndex = foundIndex;
                    }
                });
                
                lastCutIndex = 0;
                let activeElementIndex = 0;
                
                const sentences = text.match(/[^.!?]+[.!?]+/g) || [text];
                
                sentences.forEach((sen) => {
                    let senLower = sen.toLowerCase();
                    let elemsToReveal = [];
                    
                    while (activeElementIndex < syncElements.length) {
                        const elText = syncElements[activeElementIndex].textContent.trim().toLowerCase();
                        const cleanText = elText.replace(/[.,/#!$%^&*;:{}=\-_`~()]/g,"");
                        const words = cleanText.split(/\\s+/).filter(w => w.length > 3).slice(0, 2);
                        
                        if (words.length > 0 && senLower.includes(words[0])) {
                            elemsToReveal.push(syncElements[activeElementIndex]);
                            activeElementIndex++;
                        } else {
                            break;
                        }
                    }
                    
                    if (sen.trim()) {
                        chunks.push({ text: sen.trim(), elementsToReveal: elemsToReveal });
                    }
                });
                
                while (activeElementIndex < syncElements.length) {
                    if (chunks.length > 0) {
                        chunks[chunks.length-1].elementsToReveal.push(syncElements[activeElementIndex]);
                    }
                    activeElementIndex++;
                }
            } else {
                chunks.push({ text: text, elementsToReveal: [] });
            }

            if (ttsEngine === 'default') {
                const utterance = new SpeechSynthesisUtterance(text);
                const voices = window.speechSynthesis.getVoices();
                const voiceObj = voices.find(v => v.name === selectedVoice);
                if (voiceObj) utterance.voice = voiceObj;
                utterance.rate = ttsState.rate;
                
                utterance.onstart = () => {
                    ttsState.isPlaying = true;
                    updateTTSButtons();
                    triggerFallbackReveal();
                };
                utterance.onend = () => {
                    if (ttsState.isPlaying) {
                        if (currentSlide < slides.length - 1) {
                            currentSlide++;
                            setTimeout(() => {
                                if (ttsState.isPlaying) renderSlide(currentSlide);
                            }, 800);
                        } else {
                            ttsState.isPlaying = false;
                            updateTTSButtons();
                        }
                    }
                };
                if (ttsState.isPlaying) window.speechSynthesis.speak(utterance);
                return;
            }

            async function fetchAudioForChunk(chunkText) {
                const requestBody = {
                    text: chunkText,
                    voice: selectedVoice,
                    tts_engine: ttsEngine
                };
                const res = await fetch('/generate-audio', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });
                if (!res.ok) throw new Error("Audio chunk generation failed");
                const data = await res.json();
                if (data.status !== 'success') throw new Error(data.detail);
                return data.audio_url;
            }

            ttsState.isPlaying = true;
            updateTTSButtons();

            try {
                let nextAudioPromise = fetchAudioForChunk(chunks[0].text);

                for (let i = 0; i < chunks.length; i++) {
                    if (window.currentNarrationId !== thisNarrationId) return;

                    const audioUrl = await nextAudioPromise;
                    
                    if (i + 1 < chunks.length) {
                        nextAudioPromise = fetchAudioForChunk(chunks[i + 1].text);
                    }

                    if (window.currentNarrationId !== thisNarrationId) return;

                    const audio = new Audio(audioUrl);
                    audio.crossOrigin = "anonymous";
                    audio.style.display = 'none';
                    document.body.appendChild(audio);
                    
                    window.currentAudio = audio;
                    audio.playbackRate = ttsState.rate;

                    if (chunks[i].elementsToReveal && chunks[i].elementsToReveal.length > 0) {
                        chunks[i].elementsToReveal.forEach(el => {
                            if (el) {
                                el.classList.remove('cascade-hidden');
                                el.classList.add('cascade-visible');
                            }
                        });
                    }

                    await new Promise((resolve, reject) => {
                        audio.addEventListener('ended', resolve);
                        audio.addEventListener('error', reject);
                        
                        const checkPlayState = setInterval(() => {
                            if (!ttsState.isPlaying && !audio.paused) {
                                audio.pause();
                            } else if (ttsState.isPlaying && audio.paused) {
                                audio.play().catch(e => console.log("Auto-play prevented", e));
                            }
                            if (window.currentNarrationId !== thisNarrationId) {
                                clearInterval(checkPlayState);
                                audio.pause();
                                resolve();
                            }
                        }, 100);

                        audio.addEventListener('ended', () => clearInterval(checkPlayState));
                        audio.addEventListener('error', () => clearInterval(checkPlayState));

                        if (ttsState.isPlaying) {
                            audio.play().catch(reject);
                        }
                    });
                    
                    if (audio.parentNode) {
                        audio.parentNode.removeChild(audio);
                    }
                }
                
                if (window.currentNarrationId !== thisNarrationId) return;
                
                if (ttsState.isPlaying) {
                    if (currentSlide < slides.length - 1) {
                        currentSlide++;
                        setTimeout(() => {
                            if (ttsState.isPlaying) renderSlide(currentSlide);
                        }, 800);
                    } else {
                        ttsState.isPlaying = false;
                        updateTTSButtons();
                    }
                }

            } catch (err) {
                console.error("Realtime Chunk API Sync Error:", err);
                triggerFallbackReveal();
                ttsState.isPlaying = false;
                updateTTSButtons();
            }
        }
"""
        # Safely splice strings to avoid replace bugs
        new_content = content[:start_idx] + new_block + content[end_idx:]
        
        with open('index.html', 'w', encoding='utf-16') as f:
            f.write(new_content)
        print('Patched index.html safely successfully.')
