import re

with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove the fade-out logic
content = content.replace(\"\"\"            // 1. Fade Out Content First (no clear cuts)
            container.style.opacity = '0';
            if (visualContainer) visualContainer.style.opacity = '0';
            if (titleContainer) titleContainer.style.opacity = '0';
            
            // Wait for fade out to complete before morphing the board and swapping content
            setTimeout(() => {
                
                const isGoingFullscreen = (slide.type === 'ai_video' || slide.type === 'visual' || slide.type === 'simulation');
                const wasFullscreen = board.classList.contains('cinematic-fullscreen');
                
                // FLIP Step 1: Lock current height in pixels to prevent CSS snapping
                let startPx = board.offsetHeight;
                if (isGoingFullscreen !== wasFullscreen) {
                    board.style.height = startPx + 'px';
                    void board.offsetHeight; // Force reflow
                }

                // Remove old classes\"\"\", \"\"\"            const isGoingFullscreen = (slide.type === 'ai_video' || slide.type === 'visual' || slide.type === 'simulation');
            const wasFullscreen = board.classList.contains('cinematic-fullscreen');

            const performDOMUpdate = () => {
                if (isGoingFullscreen) {
                    board.style.height = '';
                } else {
                    board.style.height = 'fit-content';
                }

                container.style.opacity = '1';
                if (visualContainer) visualContainer.style.opacity = '1';
                if (titleContainer) titleContainer.style.opacity = '1';

                // Remove old classes\"\"\")

# Remove FLIP step 2 completely and the settimeout 50
content = content.replace(\"\"\"                // FLIP Step 2: Transition to new state smoothly
                if (isGoingFullscreen !== wasFullscreen) {
                    if (isGoingFullscreen) {
                        board.classList.add('cinematic-fullscreen');
                        document.body.classList.remove('text-mode-active');
                        board.style.height = ''; // Let !important 100vh take over seamlessly
                    } else {
                        // We are returning to text mode. We need to find the new fit-content height
                        board.classList.remove('cinematic-fullscreen');
                        document.body.classList.add('text-mode-active');
                        
                        // Measure silently
                        board.style.transition = 'none';
                        board.style.height = 'fit-content';
                        const targetPx = board.offsetHeight;
                        
                        // Revert to start and force reflow
                        board.style.height = startPx + 'px';
                        void board.offsetHeight;
                        
                        // Animate to target
                        board.style.transition = '';
                        board.style.height = targetPx + 'px';
                        
                        // Clear inline height after animation finishes
                        setTimeout(() => {
                            if (!board.classList.contains('cinematic-fullscreen')) {
                                board.style.height = 'fit-content';
                            }
                        }, 500);
                    }
                }

                // 3. Trigger Fade In
                setTimeout(() => {
                    container.style.opacity = '1';
                    if (visualContainer && (slide.type === 'ai_video' || slide.type === 'visual' || slide.type === 'simulation')) {
                        visualContainer.style.opacity = '1';
                    }
                }, 50);\"\"\", \"\"\"                if (isGoingFullscreen) {
                    board.classList.add('cinematic-fullscreen');
                    document.body.classList.remove('text-mode-active');
                } else {
                    board.classList.remove('cinematic-fullscreen');
                    document.body.classList.add('text-mode-active');
                }\"\"\")

# Replace the closing brackets of the setTimeout
content = content.replace(\"\"\"                // Speech narration trigger
                if (slide.type === 'ai_video' && slide.ai_audio_source === 'video') {
                    // Video plays natively with audio, rely on its onended listener for auto-advance.
                } else if (slide.narration) {
                    speakNarration(slide.narration, Array.from(animateItems));
                } else {
                    // Fallback reveal if no TTS
                    animateItems.forEach(item => item.classList.add('revealed'));
                    updateTTSButtons();
                    
                    // Fallback auto-advance for silent slides if exporting
                    if (isAutoExporting) {
                        setTimeout(() => {
                            if (currentSlide < slides.length - 1) {
                                currentSlide++;
                                renderSlide(currentSlide);
                            } else {
                                if (autoExportRecorder && autoExportRecorder.state === 'recording') {
                                    autoExportRecorder.stop();
                                }
                            }
                        }, 5000); // 5 sec fallback for silent slides
                    }
                }

            }, 300); // End of Fade Out Timeout
        }\"\"\", \"\"\"                // Speech narration trigger
                if (slide.type === 'ai_video' && slide.ai_audio_source === 'video') {
                } else if (slide.narration) {
                    speakNarration(slide.narration, Array.from(animateItems));
                } else {
                    animateItems.forEach(item => item.classList.add('revealed'));
                    updateTTSButtons();
                    if (isAutoExporting) {
                        setTimeout(() => {
                            if (currentSlide < slides.length - 1) {
                                currentSlide++;
                                renderSlide(currentSlide);
                            } else {
                                if (autoExportRecorder && autoExportRecorder.state === 'recording') autoExportRecorder.stop();
                            }
                        }, 5000);
                    }
                }
            };

            if (document.startViewTransition && (isGoingFullscreen !== wasFullscreen)) {
                const transition = document.startViewTransition(() => {
                    performDOMUpdate();
                });
                transition.ready.then(() => {
                    finalizeSlideAnimations();
                });
            } else {
                performDOMUpdate();
                finalizeSlideAnimations();
            }
        }\"\"\")

# Create the finalize function wrapper
content = content.replace(\"\"\"                // Staggered reveal for list items, paragraphs, and table rows
                const animateItems = container.querySelectorAll('h2, h3, p, li, tbody tr, .math-block, .definition, .formula-block');\"\"\", \"\"\"            };

            const finalizeSlideAnimations = () => {
                // Staggered reveal for list items, paragraphs, and table rows
                const animateItems = container.querySelectorAll('h2, h3, p, li, tbody tr, .math-block, .definition, .formula-block');\"\"\")


with open('index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Update successful')
