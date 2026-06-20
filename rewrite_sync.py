import re

with open("c:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/index.html", "r", encoding="utf-8") as f:
    content = f.read()

# Locate the Advanced Keyword-Alignment Sync block
start_marker = "// Advanced Keyword-Alignment Sync"
end_marker = "audio.addEventListener('timeupdate', () => {"

sync_start_idx = content.find(start_marker)
sync_end_idx = content.find(end_marker)

# Extract it
sync_logic = content[sync_start_idx:sync_end_idx]

# Remove it from its original place
content = content[:sync_start_idx] + content[sync_end_idx:]

# Locate the target insertion point (right before `if (ttsEngine === 'default') {`)
insertion_marker = "const ttsEngine = document.getElementById('tts-engine-select') ? document.getElementById('tts-engine-select').value : 'default';"
insert_idx = content.find(insertion_marker) + len(insertion_marker) + 1

# Add it there
content = content[:insert_idx] + "\n" + sync_logic + "\n" + content[insert_idx:]

# Now replace the 'Standard (Browser TTS)' behavior
old_browser_tts = """
                const selectedVoice = ttsEngine === 'default' 
                                        ? document.getElementById('voice-select').value 
                                        : document.getElementById('gemini-voice-select').value;
                
                // Use native browser TTS if standard is selected
                if (ttsEngine === 'default') {
                    const utterance = new SpeechSynthesisUtterance(text);
                    const voices = window.speechSynthesis.getVoices();
                    const voiceObj = voices.find(v => v.name === selectedVoice);
                    if (voiceObj) utterance.voice = voiceObj;
                    utterance.rate = ttsState.rate;
                    
                    // Handle word boundary syncing
                    utterance.onboundary = (event) => {
                        if (event.name === 'word' && syncElements && syncElements.length > 0) {
                            const currentWord = text.substring(event.charIndex).split(/\\s+/)[0].toLowerCase();
                            // Simple fallback reveal based on progress
                            const progress = event.charIndex / text.length;
                            const targetIndex = Math.floor(progress * syncElements.length);
                            if (targetIndex > revealedCount) {
                                for (let i = revealedCount; i < targetIndex && i < syncElements.length; i++) {
                                    syncElements[i].classList.remove('cascade-hidden');
                                    syncElements[i].classList.add('cascade-visible');
                                }
                                revealedCount = targetIndex;
                            }
                        }
                    };
                    
                    utterance.onend = () => {
                        if (revealedCount < syncElements.length) triggerFallbackReveal();
                        if (ttsState.isPlaying) {
                            if (isAutoExporting && autoExportRecorder && autoExportRecorder.state === 'recording') {
                                // Delay slightly before next slide for pacing
                                setTimeout(() => {
                                    if (currentSlide < slides.length - 1) {
                                        currentSlide++;
                                        renderSlide(currentSlide);
                                    } else {
                                        autoExportRecorder.stop();
                                    }
                                }, 1000);
                            }
                        }
                    };
                    
                    window.currentAudio = {
                        pause: () => window.speechSynthesis.cancel(),
                        play: () => {
                            if (ttsState.isPlaying) window.speechSynthesis.speak(utterance);
                        },
                        set playbackRate(val) { utterance.rate = val; }
                    };
                    
                    if (ttsState.isPlaying) window.speechSynthesis.speak(utterance);
                    return;
                }
"""

new_browser_tts = """
                const selectedVoice = ttsEngine === 'default' 
                                        ? document.getElementById('voice-select').value 
                                        : document.getElementById('gemini-voice-select').value;
                
                // Use native browser TTS if standard is selected
                if (ttsEngine === 'default') {
                    const utterance = new SpeechSynthesisUtterance(text);
                    const voices = window.speechSynthesis.getVoices();
                    const voiceObj = voices.find(v => v.name === selectedVoice);
                    if (voiceObj) utterance.voice = voiceObj;
                    utterance.rate = ttsState.rate;
                    
                    let fallbackTimer = null;

                    utterance.onstart = () => {
                        ttsState.isPlaying = true;
                        updateTTSButtons();
                        if (syncElements && syncElements.length > 0 && revealedCount === 0) {
                            syncElements[0].classList.remove('cascade-hidden');
                            syncElements[0].classList.add('cascade-visible');
                            revealedCount = 1;
                        }
                        
                        // Safety net
                        fallbackTimer = setTimeout(() => {
                            if (revealedCount < syncElements.length) {
                                triggerFallbackReveal();
                            }
                        }, 3000);
                    };

                    // Handle word boundary syncing with advanced Thresholds
                    utterance.onboundary = (event) => {
                        if (event.name === 'word' && syncElements && syncElements.length > 0) {
                            const progress = event.charIndex / text.length;
                            
                            syncElements.forEach((el, index) => {
                                if (el.classList.contains('cascade-visible')) return;
                                
                                const threshold = elementThresholds[index];
                                if (progress >= threshold) {
                                    el.classList.remove('cascade-hidden');
                                    el.classList.add('cascade-visible');
                                    revealedCount++;
                                }
                            });
                        }
                    };
                    
                    utterance.onend = () => {
                        if (fallbackTimer) clearTimeout(fallbackTimer);
                        if (revealedCount < syncElements.length) triggerFallbackReveal();
                        
                        if (ttsState.isPlaying) {
                            if (currentSlide < slides.length - 1) {
                                currentSlide++;
                                setTimeout(() => {
                                    if (ttsState.isPlaying) renderSlide(currentSlide);
                                }, 800);
                            } else {
                                ttsState.isPlaying = false;
                                updateTTSButtons();
                                if (isAutoExporting && autoExportRecorder && autoExportRecorder.state === 'recording') {
                                    autoExportRecorder.stop();
                                }
                            }
                        }
                    };
                    
                    window.currentAudio = {
                        pause: () => window.speechSynthesis.cancel(),
                        play: () => {
                            if (ttsState.isPlaying) window.speechSynthesis.speak(utterance);
                        },
                        set playbackRate(val) { utterance.rate = val; }
                    };
                    
                    if (ttsState.isPlaying) window.speechSynthesis.speak(utterance);
                    return;
                }
"""

content = content.replace(old_browser_tts.strip(), new_browser_tts.strip())

with open("c:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/index.html", "w", encoding="utf-8") as f:
    f.write(content)
print("Done.")
